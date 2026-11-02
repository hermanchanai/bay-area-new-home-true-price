"""derive_agent_truth: its own ready-date reading and ARM worth, checked against hand-worked figures."""
import pytest

import tools.derive_agent_truth as dat
from tools.derive_agent_truth import arm_worth, ranked, ready_month


def test_ready_month_reads_builder_dates_and_ready_now_is_the_freeze_month():
    today = "2026-10-30"
    assert ready_month(None, "now", today) == "2026-10"
    assert ready_month("2026-08-26", "now", today) == "2026-10"       # ready since August = ready now
    assert ready_month("11/30/2026", "month", today) == "2026-11"
    assert ready_month("February 2027", "under_construction", today) == "2027-02"
    assert ready_month("Dec/Jan", "month", today) == "2027-01"       # the later month, next occurrence
    assert ready_month("Nov/Dec", "month", today) == "2026-12"
    assert ready_month("Apr.", "month", today) == "2027-04"
    assert ready_month("Oct. Move In.", "month", today) == "2026-10"
    assert ready_month(None, "under_construction", today) is None     # unknown: left out


def test_arm_worth_matches_the_hand_check_for_1576_torrey_at_10_pct_down():
    # $717,215, 4.99% fixed 7 years vs 7.24%: worth $61,657 (agent's reprice tool gave the same on 10-30)
    assert arm_worth(717_215, 0.10, 7.24, 4.99, 7) == 61_657


def test_ranked_keeps_ties_with_the_last_place():
    hs = [{"id": i, "name": n, "true_price": p} for i, (n, p) in enumerate([("a", 1), ("b", 2), ("c", 3), ("d", 3), ("e", 9)])]
    assert ranked(hs) == ["a", "b", "c", "d"]


def test_explicit_years_across_a_range_go_with_the_last_month():
    """Codex review 1 (chunk 4) #7: 'Dec 2026 / Jan 2027' read as 2026-01."""
    assert ready_month("Dec 2026 / Jan 2027", "month", "2026-10-30") == "2027-01"


def test_a_promo_rate_at_or_above_market_is_worth_nothing_and_a_zero_rate_works():
    """Codex review 1 (chunk 4) #3: a 4.99% promo against a 4.50% market came out at -$13,711 (the product
    says $0); a 0% rate divided by zero."""
    assert arm_worth(717_215, 0.10, 4.50, 4.99, 7) == 0
    assert arm_worth(717_215, 0.10, 4.99, 4.99, 7) == 0
    assert arm_worth(360_000, 0.0, 6.0, 0.0, 1) > 0


def test_a_tie_for_cheapest_is_any_of_not_all_of():
    """Codex review 1 (chunk 4) #6: four homes tied for cheapest became four required names in a top 3."""
    hs = [{"id": i, "name": f"h{i}", "true_price": 400_000} for i in range(4)]
    assert dat.cheapest_expect(hs) == {"top3_any_of": ["h0", "h1", "h2", "h3"]}
    assert dat.cheapest_expect(hs[:1] + [{"id": 9, "name": "x", "true_price": 9}][:0]) == {"top3_all_of": ["h0"]}


def test_header_notes_are_one_comment_line_each():
    """Codex review 1 (chunk 4) #2: a label with a newline left an uncommented line and broke the YAML."""
    assert dat.comment("q09 offers: Up to $5K\non select homes") == "#   q09 offers: Up to $5K on select homes"


def test_freeze_refuses_to_overwrite_an_existing_snapshot(tmp_path, monkeypatch):
    """Codex review 1 (chunk 4) #1: a second --freeze the same day overwrote the copy the key points at, then
    failed to derive, leaving the old key on new data."""
    monkeypatch.setattr(dat, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / f"eval_{dat.date.today().isoformat()}.db").write_bytes(b"old snapshot")
    monkeypatch.setattr(dat, "KEY", tmp_path / "key.yaml")
    (tmp_path / "key.yaml").write_text("db: data/eval_2026-10-30.db\nquestions: []\n")
    with pytest.raises(SystemExit, match="exists"):
        dat.main(freeze=True)
    assert (tmp_path / "data" / f"eval_{dat.date.today().isoformat()}.db").read_bytes() == b"old snapshot"


def test_the_key_asks_for_every_downstairs_plan_and_for_what_the_lilac_offers_are_worth():
    """Codex review 1 (chunk 4) #4/#5: q16 dropped Residence 1X; q09 passed '4.99%' with no worth and failed a
    correct 'FLEX Cash is no longer offered'."""
    import yaml
    key = {q["id"]: q["expect"] for q in yaml.safe_load((dat.KEY).read_text())["questions"]}
    assert "Residence 1X" in key["q16"]["must_name"]
    assert "must_not_mention" not in key["q09"]
    assert key["q09"]["must_mention_amount"] and key["q09"]["must_mention"] == ["4.99"]


def test_a_range_with_only_its_first_year_rolls_into_the_next_year():
    """Codex review 2 B#1: 'Dec 2026 / Jan' read as 2026-01."""
    assert ready_month("Dec 2026 / Jan", "month", "2026-10-30") == "2027-01"
    assert ready_month("Dec/Jan 2027", "month", "2026-10-30") == "2027-01"
    assert ready_month("Nov 2026 - Dec", "month", "2026-10-30") == "2026-12"


def test_q09_and_q13_to_q15_accept_any_written_form_of_the_amount():
    """Codex review 2 B#3: '$54.8k to $64.2k' failed q09's exact-string list; amounts are matched as numbers at
    the precision they are written in (the same reading the trace check uses)."""
    import yaml
    key = {q["id"]: q["expect"] for q in yaml.safe_load((dat.KEY).read_text())["questions"]}
    for qid in ("q09", "q13", "q14", "q15"):
        assert "must_mention_amount" in key[qid], qid
    assert key["q16"].get("must_name") and "must_mention" not in key["q16"]


def test_a_year_written_before_the_month_is_kept():
    """Codex review 3 B#1: '2027 November' read as 2026-11."""
    assert ready_month("2027 November", "month", "2026-10-30") == "2027-11"
    assert ready_month("2027 Nov / Dec", "month", "2026-10-30") == "2027-12"
