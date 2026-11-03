"""One model client for every Meta-model call: disk cache, cost ledger, budget guard.

    python -m tools.llm --spend     # spend per provider/model against each cap
    python -m tools.llm --ping      # one tiny call: checks the key and request format
    python -m tools.llm --ping --provider fireworks    # same, on a Muse Spark Open host

Providers speak OpenAI-style chat completions (Meta Model API: POST /v1/chat/completions).
Model ids, prices and caps live in config/llm.yaml.
"""
import argparse
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from tools import db

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "llm.yaml"
CACHE_DIR = ROOT / ".tmp" / "llm_cache"
RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 4

log = logging.getLogger(__name__)
_held: dict[str, float] = {}     # provider -> worst case of calls in flight in this process, not yet in the ledger
_held_lock = threading.Lock()


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class Result:
    text: str
    usage: dict
    cost_usd: float
    from_cache: bool
    model: str
    finish_reason: str | None
    tool_calls: list | None = None


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


def cost_usd(price: dict, prompt: int, cached: int, completion: int) -> float:
    """USD for one call. Prices are per 1M tokens; cached prompt tokens bill at the cached rate."""
    return ((prompt - cached) * price["input"] + cached * price["cached_input"]
            + completion * price["output"]) / 1e6


def spent(conn, provider: str, since: str | None = None) -> float:
    sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE provider = ?", [provider]
    if since:
        sql += " AND created_at >= ?"
        args.append(since)
    return conn.execute(sql, args).fetchone()[0]


def parse_json(text: str):
    """JSON from a model reply, tolerating a ```json fence around it."""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return json.loads(m.group(1) if m else text)


def _usage_counts(usage: dict) -> tuple[int, int, int, int]:
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return prompt, cached, completion, reasoning


def _record(conn, *, provider, model, purpose, key, usage=None, cost=0.0, latency_ms=None,
            ok=True, error=None, from_cache=False):
    prompt, cached, completion, reasoning = _usage_counts(usage or {})
    with conn:
        conn.execute(
            "INSERT INTO llm_calls (provider, model, purpose, cache_key, in_tokens, out_tokens, "
            "cached_tokens, reasoning_tokens, cost_usd, latency_ms, ok, error, from_cache, usage_json, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (provider, model, purpose, key, prompt, completion, cached, reasoning, cost, latency_ms,
             int(ok), error, int(from_cache), json.dumps(usage) if usage else None,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))


def chat(messages: list[dict], *, purpose: str, provider: str | None = None, model: str | None = None,
         response_format: dict | None = None, tools: list | None = None, reasoning_effort: str | None = None,
         max_completion_tokens: int | None = None, conn=None, cfg: dict | None = None,
         cache_dir: Path = CACHE_DIR, session=None, timeout: int = 600) -> Result:
    cfg = cfg or load_config()
    p = cfg["purposes"].get(purpose, {})
    provider = provider or p["provider"]
    prov = cfg["providers"][provider]
    model = model or p["model"]
    body = {"model": model, "messages": messages}
    effort = reasoning_effort or p.get("reasoning_effort")
    max_out = max_completion_tokens or p.get("max_completion_tokens")
    if effort:
        body["reasoning_effort"] = effort
    if max_out:
        body["max_completion_tokens"] = max_out
    if response_format:
        body["response_format"] = response_format
    if tools:
        body["tools"] = tools

    key = hashlib.sha256(json.dumps({"provider": provider, **body}, sort_keys=True).encode()).hexdigest()
    conn = conn or db.connect()
    cache_file = Path(cache_dir) / f"{key}.json"
    if cache_file.exists():
        hit = json.loads(cache_file.read_text())
        _record(conn, provider=provider, model=model, purpose=purpose, key=key, from_cache=True)
        return Result(hit["text"], hit["usage"], 0.0, True, model, hit.get("finish_reason"), hit.get("tool_calls"))

    # Budget guard: refuse unless the worst case (estimated input + full output allowance) fits.
    price = prov["models"].get(model) or {}
    if not {"input", "cached_input", "output"} <= price.keys():
        raise BudgetExceeded(f"{provider}/{model} has no price in config; refusing to call it")
    est_in = int(len(json.dumps(messages)) / cfg.get("chars_per_token", 3.0))
    worst = cost_usd(price, est_in, 0, max_out or 32000)
    with _held_lock:      # parallel workers: a call in flight holds its worst case until it records (review 4)
        held = _held.get(provider, 0.0)
        used = spent(conn, provider) + held
        if used + worst > prov["cap_usd"]:
            raise BudgetExceeded(f"{provider}: spent ${used:.4f} (incl. ${held:.4f} in flight) + worst case "
                                 f"${worst:.4f} > cap ${prov['cap_usd']:.2f}")
        if prov.get("daily_cap_usd") is not None:     # UTC day; the daily price check's ceiling
            today = spent(conn, provider, datetime.now(timezone.utc).date().isoformat()) + held
            if today + worst > prov["daily_cap_usd"]:
                raise BudgetExceeded(f"{provider}: spent ${today:.4f} today (incl. ${held:.4f} in flight) + worst "
                                     f"case ${worst:.4f} > daily cap ${prov['daily_cap_usd']:.2f}")
        _held[provider] = held + worst
    try:
        return _send(conn, body, prov, provider, model, purpose, key, price, est_in, cache_file, session, timeout)
    finally:
        with _held_lock:
            _held[provider] -= worst


def _send(conn, body, prov, provider, model, purpose, key, price, est_in, cache_file, session, timeout) -> Result:
    load_dotenv(ROOT / ".env", override=True)
    api_key = os.environ.get(prov["key_env"])
    if not api_key:
        raise LLMError(f"{prov['key_env']} is not set in .env")
    log.info("%s %s purpose=%s key=%s est_in=%d", provider, model, purpose, prov["key_env"], est_in)
    session = session or requests.Session()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = prov["base_url"].rstrip("/") + "/chat/completions"

    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.monotonic()
        try:
            resp = session.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            resp, err = None, f"{type(e).__name__}: {e}"
        latency = int((time.monotonic() - t0) * 1000)
        if resp is not None and resp.status_code == 200:
            break
        if resp is not None:
            err = f"HTTP {resp.status_code}: {resp.text[:500]}"
        retry = resp is None or resp.status_code in RETRY_STATUS
        if not retry or attempt == ATTEMPTS:
            _record(conn, provider=provider, model=model, purpose=purpose, key=key,
                    latency_ms=latency, ok=False, error=err)
            raise LLMError(err)
        wait = 2 ** attempt
        log.warning("%s; retry %d in %ds", err, attempt, wait)
        time.sleep(wait)

    data = resp.json()
    usage = data.get("usage") or {}
    prompt, cached, completion, _ = _usage_counts(usage)
    cost = cost_usd(price, prompt, cached, completion)
    choice = data["choices"][0]
    text = choice["message"].get("content") or ""
    finish = choice.get("finish_reason")
    tool_calls = choice["message"].get("tool_calls") or None
    _record(conn, provider=provider, model=model, purpose=purpose, key=key, usage=usage,
            cost=cost, latency_ms=latency)
    if finish in ("stop", "tool_calls"):  # only complete replies are worth replaying
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"text": text, "usage": usage, "finish_reason": finish,
                                          "tool_calls": tool_calls}))
    return Result(text, usage, cost, False, model, finish, tool_calls)


def print_spend(conn=None, cfg=None):
    cfg, conn = cfg or load_config(), conn or db.connect()
    for name, prov in cfg["providers"].items():
        print(f"{name}: ${spent(conn, name):.4f} of ${prov['cap_usd']:.2f} cap")
        for r in conn.execute(
                "SELECT model, COUNT(*) n, SUM(from_cache) hits, SUM(ok = 0) failed, "
                "SUM(in_tokens) i, SUM(out_tokens) o, SUM(reasoning_tokens) rz, SUM(cost_usd) c "
                "FROM llm_calls WHERE provider = ? GROUP BY model", (name,)):
            print(f"  {r['model']}: {r['n']} calls ({r['hits']} cached, {r['failed']} failed), "
                  f"in {r['i'] or 0:,} / out {r['o'] or 0:,} (reasoning {r['rz'] or 0:,}) tokens, "
                  f"${r['c'] or 0:.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--spend", action="store_true")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--provider", help="ping another provider (its first model in config/llm.yaml)")
    a = ap.parse_args()
    if a.ping:
        model = next(iter(load_config()["providers"][a.provider]["models"])) if a.provider else None
        r = chat([{"role": "user", "content": 'Reply with the JSON {"ok": true} and nothing else.'}],
                 purpose="ping", provider=a.provider, model=model)
        print(f"reply={r.text!r} finish={r.finish_reason} usage={r.usage} cost=${r.cost_usd:.6f}")
    print_spend()
