"""derive_agent_truth: its own ready-date reading and ARM worth, checked against hand-worked figures."""
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
