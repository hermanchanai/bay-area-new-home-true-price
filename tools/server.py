"""The site and the live agent endpoint.

    python -m tools.server                     # http://localhost:8787, serves web/ and /api/*
    gunicorn -w 1 --threads 4 'tools.server:create_app()'

POST /api/ask {"question": ...}
  1. An example question (web/data/demo_answers.json, hand-checked eval answers) is answered from that file:
     free, not rate-limited.
  2. The same question asked again today is answered from memory.
  3. Otherwise the agent runs on Muse Spark Open via Fireworks (purpose agent_live; prompts not stored),
     behind a per-IP hourly and daily limit and a daily and total spend cap (config/llm.yaml `live`), summed
     from this server's own llm_calls ledger. A cap is checked before a question, so one question can overshoot
     it by its own cost (cents).
GET /api/reprice?home_id=&down=   the incentive math with the buyer's own down payment (no model, $0)
GET /api/health                   the live model, counts and today's spend

The visitor IP is Netlify's client header when proxied, else X-Forwarded-For, else the socket. Those headers
can be forged by a caller hitting this server directly; the spend caps are the backstop. Questions are not
logged, only their length.
"""
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from tools import db, llm
from tools.agent import run as run_agent
from tools.agent_tools import describe_steps, reprice_home
from tools.export_site_data import SITE_DB, WEB, question_key

ROOT = Path(__file__).resolve().parent.parent
PURPOSE = "agent_live"
log = logging.getLogger(__name__)


def client_ip(req) -> str:
    fwd = req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return req.headers.get("X-Nf-Client-Connection-Ip") or fwd or req.remote_addr or "?"


def _spent(conn, since: str | None = None) -> float:
    sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE purpose = ?"
    args = [PURPOSE]
    if since:
        sql += " AND created_at >= ?"
        args.append(since)
    return round(conn.execute(sql, args).fetchone()[0], 4)


def create_app(db_path: Path = SITE_DB, run=run_agent, live: dict | None = None, demo: dict | None = None,
               clock=time.time) -> Flask:
    cfg = llm.load_config()
    live = live or cfg["live"]
    model = "{provider}/{model}".format(**cfg["purposes"][PURPOSE])
    if demo is None:
        f = WEB / "demo_answers.json"
        demo = json.loads(f.read_text()) if f.exists() else {}
    app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="")
    hits = defaultdict(deque)            # ip -> times of live questions, last 24 h
    answered = {}                        # (utc day, question_key) -> answer
    lock = threading.Lock()

    def today() -> str:
        return datetime.fromtimestamp(clock(), UTC).date().isoformat()

    def limited(ip: str) -> str | None:
        now = clock()
        with lock:
            q = hits[ip]
            while q and now - q[0] > 86_400:
                q.popleft()
            if sum(now - t <= 3600 for t in q) >= live["per_ip_per_hour"]:
                return f"You've asked {live['per_ip_per_hour']} new questions this hour. Try the examples, or come back later."
            if len(q) >= live["per_ip_per_day"]:
                return f"You've asked {live['per_ip_per_day']} new questions today. Try the examples, or come back tomorrow."
            q.append(now)
        return None

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.post("/api/ask")
    def ask():
        body = request.get_json(silent=True)
        q = (body or {}).get("question") if isinstance(body, dict) else None
        if not isinstance(q, str) or not q.strip():
            return jsonify(error="Ask a question about new homes in the four counties."), 400
        q = q.strip()
        if len(q) > live["max_question_chars"]:
            return jsonify(error=f"Keep it under {live['max_question_chars']} characters."), 400
        key = question_key(q)
        if key in demo:
            return jsonify(answer=demo[key]["answer"], source="checked", checked=demo[key].get("checked"), cost_usd=0,
                           steps=demo[key].get("steps", []))
        if (today(), key) in answered:
            answer, steps = answered[(today(), key)]
            return jsonify(answer=answer, source="cached", cost_usd=0, steps=steps)
        conn = db.connect(db_path)
        try:
            if _spent(conn, today()) >= live["daily_cap_usd"]:
                return jsonify(error="The agent has used today's budget. The example questions still work."), 503
            if _spent(conn) >= live["total_cap_usd"]:
                return jsonify(error="The agent has used its budget for this demo. The example questions still work."), 503
            if msg := limited(client_ip(request)):
                return jsonify(error=msg), 429
            log.info("live question, %d chars", len(q))
            try:
                out = run(q, conn=conn, purpose=PURPOSE)
            except llm.LLMError as e:
                log.warning("agent failed: %s", type(e).__name__)
                return jsonify(error="The agent is unavailable right now. The example questions still work."), 503
        finally:
            conn.close()
        steps = describe_steps(out["trace"])
        answered[(today(), key)] = (out["answer"], steps)
        return jsonify(answer=out["answer"], source="live", cost_usd=round(out["cost_usd"], 4), turns=out["turns"],
                       steps=steps)

    @app.get("/api/reprice")
    def reprice():
        conn = db.connect(db_path)
        try:
            return jsonify(reprice_home(conn, int(request.args["home_id"]), float(request.args.get("down", 20))))
        except (KeyError, ValueError) as e:
            return jsonify(error=str(e)), 400
        finally:
            conn.close()

    @app.get("/api/health")
    def health():
        conn = db.connect(db_path)
        try:
            return jsonify(ok=True, agent_model=model, homes=conn.execute("SELECT COUNT(*) FROM homes").fetchone()[0],
                           spent_today_usd=_spent(conn, today()), daily_cap_usd=live["daily_cap_usd"],
                           spent_total_usd=_spent(conn), total_cap_usd=live["total_cap_usd"])
        finally:
            conn.close()

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    create_app().run(port=int(os.environ.get("PORT", "8787")), threaded=True)
