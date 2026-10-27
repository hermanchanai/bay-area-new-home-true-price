"""llm.py: cost math, budget guard, disk cache, ledger. A fake HTTP session; no network.
Expected costs are hand arithmetic from the config prices, not read back from the code."""
import json

import pytest

from tools import db, llm

CFG = {
    "providers": {
        "meta": {"base_url": "https://api.example/v1", "key_env": "TEST_KEY", "cap_usd": 1.00,
                 "models": {"m": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
                            "unpriced": {}}},
    },
    "purposes": {"t": {"provider": "meta", "model": "m", "reasoning_effort": "low",
                       "max_completion_tokens": 1000}},
    "chars_per_token": 4.0,
}


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


def ok(content="hi", prompt=1000, completion=200, cached=0, reasoning=0):
    return FakeResp(200, {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                                    "prompt_tokens_details": {"cached_tokens": cached},
                                    "completion_tokens_details": {"reasoning_tokens": reasoning}}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "k")
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    return {"conn": db.connect(":memory:"), "cache_dir": tmp_path / "cache", "cfg": CFG}


def test_cost_math():
    price = {"input": 1.25, "cached_input": 0.15, "output": 4.25}
    # 1000 prompt (400 cached) + 200 out: 600*1.25 + 400*0.15 + 200*4.25 = 750+60+850 = 1660 per 1M
    assert llm.cost_usd(price, prompt=1000, cached=400, completion=200) == pytest.approx(0.00166)


def test_call_records_ledger_and_cost(env):
    s = FakeSession(ok(prompt=1000, completion=200, reasoning=150))
    r = llm.chat([{"role": "user", "content": "x"}], purpose="t", session=s, **env)
    assert r.text == "hi" and not r.from_cache
    assert r.cost_usd == pytest.approx((1000 * 1.25 + 200 * 4.25) / 1e6)
    row = env["conn"].execute("SELECT * FROM llm_calls").fetchone()
    assert (row["provider"], row["model"], row["purpose"], row["ok"]) == ("meta", "m", "t", 1)
    assert (row["in_tokens"], row["out_tokens"], row["reasoning_tokens"]) == (1000, 200, 150)
    body = s.calls[0]["json"]
    assert body["model"] == "m" and body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 1000
    assert s.calls[0]["headers"]["Authorization"] == "Bearer k"


def test_cache_hit_sends_nothing_and_costs_zero(env):
    s = FakeSession(ok(content="first"))
    msgs = [{"role": "user", "content": "same"}]
    llm.chat(msgs, purpose="t", session=s, **env)
    r = llm.chat(msgs, purpose="t", session=s, **env)
    assert r.text == "first" and r.from_cache and r.cost_usd == 0
    assert len(s.calls) == 1
    assert llm.spent(env["conn"], "meta") == pytest.approx((1000 * 1.25 + 200 * 4.25) / 1e6)


def test_budget_guard_refuses_before_sending(env):
    # Already spent $0.999 of the $1.00 cap; the next call's worst case (input estimate +
    # 1000 output tokens * $4.25/1M = $0.00425 alone) would cross it.
    env["conn"].execute("INSERT INTO llm_calls (provider, model, purpose, cache_key, cost_usd, ok, "
                        "created_at) VALUES ('meta','m','t','x',0.999,1,'now')")
    s = FakeSession()
    with pytest.raises(llm.BudgetExceeded):
        llm.chat([{"role": "user", "content": "x"}], purpose="t", session=s, **env)
    assert s.calls == []


def test_unpriced_model_is_refused(env):
    with pytest.raises(llm.BudgetExceeded):
        llm.chat([{"role": "user", "content": "x"}], purpose="t", model="unpriced",
                 session=FakeSession(), **env)


def test_retry_on_429_then_ok(env, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    s = FakeSession(FakeResp(429, {"error": "slow down"}), ok(content="after"))
    assert llm.chat([{"role": "user", "content": "y"}], purpose="t", session=s, **env).text == "after"
    assert len(s.calls) == 2


def test_http_error_is_logged_and_raised(env, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    s = FakeSession(FakeResp(400, {"error": {"message": "bad schema"}}))
    with pytest.raises(llm.LLMError, match="bad schema"):
        llm.chat([{"role": "user", "content": "z"}], purpose="t", session=s, **env)
    row = env["conn"].execute("SELECT ok, error FROM llm_calls").fetchone()
    assert row["ok"] == 0 and "bad schema" in row["error"]


def test_parse_json_strips_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('{"a": 2}') == {"a": 2}
