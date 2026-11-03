"""server: the live agent endpoint's guards (cached answers, per-IP limits, spend caps) and re-pricing."""
import json
from datetime import UTC, datetime

import pytest

from tests.test_agent_tools import conn as _conn
from tools import db, llm
from tools.server import create_app

conn = _conn
CFG = {"daily_cap_usd": 1.0, "total_cap_usd": 5.0, "per_ip_per_hour": 2, "per_ip_per_day": 3,
       "max_question_chars": 100}


@pytest.fixture
def app(conn, tmp_path):
    path = tmp_path / "site.db"
    disk = db.connect(path)
    conn.commit()                                            # backup waits forever on an open transaction
    conn.backup(disk)
    disk.close()
    calls = []

    def fake_run(question, conn=None, purpose=None):
        calls.append((question, purpose))
        conn.execute("INSERT INTO llm_calls (provider, model, purpose, cache_key, cost_usd, ok, created_at) "
                     "VALUES ('meta', 'm', ?, 'k', 0.4, 1, ?)",
                     (purpose, datetime.now(UTC).isoformat(timespec="seconds")))
        conn.commit()
        return {"answer": f"About {question} [H1]", "turns": 2, "cost_usd": 0.4, "trace": [{"tool": "search_homes", "args": {"city": "Dublin"}, "result": {"total_matching": 3}}]}

    demo = {"homes in tracy": {"question": "Homes in Tracy?", "answer": "1 Oak St [H1]", "steps": ["Searched homes: city Tracy → 1 found"]}}
    a = create_app(db_path=path, run=fake_run, live=CFG, demo=demo)
    a.testing = True
    a.calls = calls
    return a


def ask(client, q, ip="1.1.1.1"):
    return client.post("/api/ask", json={"question": q}, headers={"X-Nf-Client-Connection-Ip": ip})


def test_example_questions_come_from_the_hand_checked_cache_for_free(app):
    c = app.test_client()
    for _ in range(5):                                       # never rate-limited, never calls the model
        r = ask(c, "  homes in TRACY ")
        assert r.status_code == 200 and r.json["answer"] == "1 Oak St [H1]" and r.json["source"] == "checked"
    assert app.calls == [] and r.json["steps"] == ["Searched homes: city Tracy → 1 found"]


def test_a_live_question_uses_standard_mode_and_repeats_are_cached(app):
    c = app.test_client()
    r = ask(c, "3 beds in Dublin?")
    assert r.status_code == 200 and r.json["source"] == "live" and r.json["answer"].startswith("About")
    assert app.calls == [("3 beds in Dublin?", "agent_live")]   # not contributor mode: Meta doesn't train on it
    assert r.json["steps"] == ["Searched homes: city Dublin → 3 found"]
    again = ask(c, "3 beds in dublin").json
    assert again["source"] == "cached" and again["steps"] == r.json["steps"]
    assert len(app.calls) == 1


def test_per_ip_hourly_limit(app):
    c = app.test_client()
    assert ask(c, "q one").status_code == 200
    assert ask(c, "q two").status_code == 200
    r = ask(c, "q three")
    assert r.status_code == 429 and "hour" in r.json["error"]
    assert ask(c, "q four", ip="2.2.2.2").status_code == 200   # another visitor is unaffected


def test_daily_spend_cap_stops_live_calls_but_not_cached_answers(app):
    c = app.test_client()
    assert ask(c, "q one", ip="1.1.1.1").status_code == 200    # $0.40
    assert ask(c, "q two", ip="2.2.2.2").status_code == 200    # $0.80
    assert ask(c, "q three", ip="3.3.3.3").status_code == 200  # $1.20: over the $1 cap after this one
    r = ask(c, "q four", ip="4.4.4.4")
    assert r.status_code == 503 and "today" in r.json["error"]
    assert len(app.calls) == 3
    assert ask(c, "homes in tracy", ip="4.4.4.4").status_code == 200


def test_bad_questions_are_refused_before_any_model_call(app):
    c = app.test_client()
    assert ask(c, "").status_code == 400
    assert ask(c, "x" * 101).status_code == 400
    assert c.post("/api/ask", data="not json").status_code == 400
    assert app.calls == []


def test_reprice_reruns_the_math_for_free(app):
    c = app.test_client()
    r = c.get("/api/reprice?home_id=1&down=10")
    assert r.status_code == 200 and r.json["assumptions"]["down_payment_pct"] == 10
    assert r.json["true_price_low"] < 900_000
    assert c.get("/api/reprice?home_id=1&down=1").status_code == 400
    assert c.get("/api/reprice?home_id=999&down=20").status_code == 400
    assert app.calls == []


def test_health_reports_spend_against_the_caps(app):
    h = app.test_client().get("/api/health").json
    assert h["ok"] and h["homes"] == 5 and h["spent_today_usd"] == 0 and h["daily_cap_usd"] == 1.0
    assert json.dumps(h)                                       # no secrets, plain JSON


def test_health_names_the_live_model():
    purpose = llm.load_config()["purposes"]["agent_live"]
    h = create_app(live={"daily_cap_usd": 1, "total_cap_usd": 1, "per_ip_per_hour": 1, "per_ip_per_day": 1,
                         "max_question_chars": 10}, demo={}).test_client().get("/api/health").json
    assert h["agent_model"] == f"{purpose['provider']}/{purpose['model']}"
