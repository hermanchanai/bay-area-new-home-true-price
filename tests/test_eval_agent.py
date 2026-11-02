"""eval_agent scoring: ranked homes from citations, money figures traced to tool results."""
from tools.eval_agent import cited, money_in, score, untraced

TRACE = [{"tool": "search_homes", "result": {"homes": [
    {"home_id": 11, "address": "1 Oak St", "asking": 1_079_990, "true_price_low": 1_020_500, "discount_pct_high": 5.5},
    {"home_id": 12, "address": "3 Oak St", "asking": 804_000}]}}]


def test_cited_ids_in_order_of_first_mention():
    text = "Best: 1 Oak St [H11], then [H12]. Community [C3]; again [H11]. Plan [P7]."
    assert cited(text) == {"H": [11, 12], "C": [3], "P": [7]}


def test_money_in_reads_dollars_and_keeps_their_printed_precision():
    assert money_in("$1,079,990 and $1.08M, $804k, $1.1M, 5.5%") == [
        (1_079_990, 1), (1_080_000, 10_000), (804_000, 1_000), (1_100_000, 100_000)]


def test_untraced_flags_figures_no_tool_returned():
    assert untraced("Asking $1,079,990, true price from $1.02M [H11]", TRACE, "") == []
    assert untraced("You'd save about $59,490 [H11]", TRACE, "") == [59_490]       # model did its own arithmetic
    assert untraced("Under your $1.1M budget", TRACE, "under $1.1M") == []         # echoing the question is fine


def test_score_needs_the_expected_homes_in_the_top_three_and_nothing_ineligible():
    q = {"id": "q", "question": "", "expect": {"top3_any_of": ["1 Oak St"], "eligible": ["1 Oak St", "3 Oak St"]}}
    homes = {11: "1 Oak St", 12: "3 Oak St", 13: "9 Elm St"}
    assert score(q, "1 Oak St [H11] or [H12]", TRACE, homes)["pass"]
    assert not score(q, "[H12] then [H13] then [H11]", TRACE, homes)["pass"]      # 9 Elm St not eligible
    assert not score(q, "[H12]", TRACE, homes)["pass"]                              # expected home missing


def test_score_must_mention_and_must_not_cite():
    q = {"id": "q", "question": "", "expect": {"must_mention": ["804"], "no_homes": True}}
    assert score(q, "Buyers paid a median of $804k.", TRACE, {})["pass"]
    assert not score(q, "Buyers paid $804k; try [H12].", TRACE, {12: "3 Oak St"})["pass"]
    assert not score(q, "Buyers paid about $790k.", TRACE, {})["pass"]           # untraced and wrong


def test_untraced_also_checks_bare_numbers_written_without_a_dollar_sign():
    # q15's first run wrote "Asking 789925": an unformatted figure must not escape the trace check
    assert untraced("Asking 1079990, true 1020500 [H11]", TRACE, "") == []
    assert untraced("Asking 1079999 [H11]", TRACE, "") == [1_079_999]
    assert untraced("Built 2024, 2178 sqft, ZIP 95391", TRACE, "") == []             # not money-sized


def test_the_eval_queries_the_frozen_db_its_answer_key_names_and_logs_spend_to_the_main_db(tmp_path, monkeypatch):
    """The daily refresh changes homes and prices, so a key derived from live data goes stale in a day
    (10-30: 5 of 6 misses). The key names a frozen copy; tools read it, the LLM ledger stays in the main DB."""
    import sqlite3

    import yaml

    import tools.eval_agent as ev
    frozen = tmp_path / "frozen.db"
    c = sqlite3.connect(frozen)
    c.executescript("CREATE TABLE homes (id INTEGER, address TEXT, lot TEXT); INSERT INTO homes VALUES (7, '1 Oak St', NULL);"
                    "CREATE TABLE communities (id INTEGER); CREATE TABLE plans (id INTEGER);")
    c.commit()
    c.close()
    key = tmp_path / "key.yaml"
    key.write_text(yaml.safe_dump({"db": str(frozen), "questions": [
        {"id": "q01", "question": "Cheapest?", "expect": {"top3_all_of": ["1 Oak St"]}}]}))
    ledger, seen = object(), {}

    def fake_run(question, conn, purpose, chat):
        seen["homes"] = conn.execute("SELECT address FROM homes").fetchall()[0][0]
        chat([], purpose=purpose, conn=conn)
        return {"answer": "1 Oak St [H7]", "trace": [], "cost_usd": 0.0, "turns": 1}

    monkeypatch.setattr(ev, "QUESTIONS", key)
    monkeypatch.setattr(ev, "run_agent", fake_run)
    monkeypatch.setattr(ev.db, "connect", lambda *a, **k: ledger)
    monkeypatch.setattr(ev.llm, "chat", lambda messages, conn=None, **k: seen.setdefault("ledger", conn))
    monkeypatch.setattr(ev, "RUNS", tmp_path / "runs")
    ev.main(only={"q01"})
    assert seen == {"homes": "1 Oak St", "ledger": ledger}


def test_amounts_match_at_their_written_precision_and_names_match_whole():
    """Codex review 2 B#2/B#3: '$54.8k' must count as $54,806; 'Residence 1' must not match inside 'Residence 1X'."""
    q = {"id": "q", "question": "", "expect": {"must_mention_amount": [[54_806, 64_219]]}}
    trace = [{"tool": "t", "result": {"v": [54_806, 64_219]}}]
    assert score(q, "Worth about $54.8k to $64.2k.", trace, {})["pass"]
    assert score(q, "Worth $64,219.", trace, {})["pass"]
    assert not score(q, "The rate is 4.99%.", trace, {})["pass"]
    q = {"id": "q", "question": "", "expect": {"must_name": ["Residence 1", "Residence 1X"]}}
    assert score(q, "Residence 1 and Residence 1X.", [], {})["pass"]
    assert not score(q, "Only Residence 1X.", [], {})["pass"]
