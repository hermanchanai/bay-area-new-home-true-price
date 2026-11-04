"""Expected values were computed by hand, not read back from the implementation. Base case: $1,000,000 price,
20% down, $800,000 loan, 30-yr amortization, market rate 6.25%. A lower rate is worth what it costs to buy
(Herman, 2026-11-03): one point (1% of the loan) per 0.25% below par; par is the market rate for a fixed loan,
0.50% lower for an ARM. Temporary buydowns stay the subsidy actually paid."""
import pytest

from tools.price_incentives import (Assumptions, Incentive, comparison_rate, monthly_payment,
                                    true_price, value_incentive)

P = 1_000_000
MKT = 6.25


def test_monthly_payment_textbook():
    assert monthly_payment(6.0, 100_000) == pytest.approx(599.55, abs=0.01)


def test_monthly_payment_base_case():
    assert monthly_payment(6.25, 800_000) == pytest.approx(4925.74, abs=0.01)
    assert monthly_payment(4.99, 800_000) == pytest.approx(4289.69, abs=0.01)


def test_fixed_rate_is_worth_the_points_to_buy_it_from_the_market_rate():
    v = value_incentive(Incentive("perm_rate", rate=4.99, structure="30yr fixed"), P, MKT)
    assert v.low == v.high == 40_320                # (6.25 - 4.99) / 0.25 = 5.04 points of $800,000


def test_the_heights_arm_from_herman_example():
    v = value_incentive(Incentive("arm_promo", rate=4.99, structure="7/6 ARM"), 1_343_166, 7.24)
    assert v.low == v.high == 75_217                # par 6.74, 7.0 points of $1,074,533


def test_two_one_buydown():
    v = value_incentive(Incentive("temp_buydown", rate=4.625, structure="2-1"), P, MKT)
    assert v.low == v.high == pytest.approx(17_351, abs=1)


def test_three_two_one_buydown():
    v = value_incentive(Incentive("temp_buydown", rate=3.5, structure="3-2-1"), P, MKT)
    assert v.low == v.high == pytest.approx(33_193, abs=1)


def test_arm_is_worth_the_points_to_buy_it_from_arm_par():
    v = value_incentive(Incentive("arm_promo", rate=4.99, structure="7/6 ARM"), P, MKT)
    assert v.low == v.high == 24_320                # par 5.75: 3.04 points of $800,000


def test_an_arm_at_or_above_arm_par_is_worth_nothing():
    v = value_incentive(Incentive("arm_promo", rate=5.9, structure="7/6 ARM"), P, MKT)
    assert (v.low, v.high) == (0, 0) and "promo_not_below_market" in v.flags


def test_first_year_rate_treated_as_one_zero_buydown():
    v = value_incentive(Incentive("first_year_rate", rate=3.75), P, MKT)
    assert v.low == v.high == pytest.approx(5_433, abs=1)
    assert "assumed_1-0" in v.flags


def test_flat_cash_up_to_is_a_range():
    v = value_incentive(Incentive("flat_cash", amount_usd=50_000, amount_is_up_to=True), P, MKT)
    assert (v.low, v.high) == (0, 50_000)


def test_flat_cash_fixed():
    v = value_incentive(Incentive("price_cut", amount_usd=61_000), P, MKT)
    assert (v.low, v.high) == (61_000, 61_000)


def test_closing_credit_up_to_is_capped_at_closing_costs():
    v = value_incentive(Incentive("closing_credit", amount_usd=50_000, amount_is_up_to=True), P, MKT)
    assert (v.low, v.high) == (0, 30_000)  # 3% of $1M


def test_promo_rate_not_below_market_is_worth_nothing():
    v = value_incentive(Incentive("perm_rate", rate=6.5), P, MKT)
    assert (v.low, v.high) == (0, 0)
    assert "promo_not_below_market" in v.flags


def test_unquantified_is_zero_and_flagged():
    v = value_incentive(Incentive("unquantified", label="Special Pricing on Select Homes"), P, MKT)
    assert (v.low, v.high) == (0, 0)
    assert "unpriced" in v.flags


def test_missing_rate_is_flagged_not_raised():
    v = value_incentive(Incentive("perm_rate"), P, MKT)
    assert (v.low, v.high) == (0, 0)
    assert "missing_input" in v.flags


def test_jumbo_loan_compares_against_jumbo_market_rate():
    a = Assumptions(conforming_limit=1_249_125)
    assert comparison_rate(2_000_000, MKT, a) == pytest.approx(6.50)
    v = value_incentive(Incentive("perm_rate", rate=4.99), 2_000_000, MKT, a)
    assert v.low == v.high == 96_640                # (6.50 - 4.99) / 0.25 = 6.04 points of $1.6M


def test_conforming_loan_keeps_market_rate():
    a = Assumptions(conforming_limit=1_249_125)
    assert comparison_rate(P, MKT, a) == pytest.approx(MKT)


def test_pool_counts_only_its_biggest_option_and_other_pools_stack():
    incs = [
        Incentive("flat_cash", amount_usd=50_000, amount_is_up_to=True, pool_id="your_way"),
        Incentive("perm_rate", rate=4.99, pool_id="your_way"),
        Incentive("closing_credit", amount_usd=2_500),
    ]
    tp = true_price(P, incs, MKT)
    assert tp.value_high == 50_000 + 2_500          # "up to" paid in full beats the rate
    assert tp.value_low == 40_320 + 2_500           # the rate, if the "up to" pays nothing
    assert tp.true_low == P - 52_500
    assert tp.true_high == P - 42_820
    assert len(tp.counted) == 2 and len(tp.not_counted) == 1


def test_no_incentives_true_price_is_asking():
    tp = true_price(P, [], MKT)
    assert (tp.true_low, tp.true_high) == (P, P)


def test_choose_one_pool_guarantees_the_best_floor_and_caps_at_the_best_ceiling():
    # "up to $250K flex cash" ($0-$250K) OR a 7/6 ARM worth a sure ~$56K: the buyer can always take the ARM
    from tools.price_incentives import Incentive, true_price
    cash = Incentive("flat_cash", amount_usd=250_000, amount_is_up_to=True, pool_id="one")
    arm = Incentive("arm_promo", rate=4.99, structure="7/6 ARM", pool_id="one")
    tp = true_price(708_990, [cash, arm], 7.32)
    arm_alone = true_price(708_990, [Incentive("arm_promo", rate=4.99, structure="7/6 ARM")], 7.32)
    assert tp.value_high == 250_000 and tp.value_low == arm_alone.value_low > 0


def test_a_rate_whose_term_is_not_stated_counts_like_an_arm():
    a = Assumptions()
    fixed = value_incentive(Incentive(kind="perm_rate", rate=5.25), 1_000_000, 7.24, a)
    v = value_incentive(Incentive(kind="perm_rate", rate=5.25, structure="term not stated"), 1_000_000, 7.24, a)
    arm = value_incentive(Incentive(kind="arm_promo", rate=5.25, structure="5/6 ARM"), 1_000_000, 7.24, a)
    assert (v.low, v.high) == (arm.low, arm.high) == (47_680, 47_680)   # par 6.74: 5.96 points of $800,000
    assert v.method == "term_unstated_points" and v.low < fixed.low


def test_an_arm_term_in_a_buydowns_structure_is_not_part_of_the_buydown():
    # "7/6 ARM, 2-1" is a 2-1 buydown: 7 and 6 aren't percentage points off the rate. Same value as a plain 2-1.
    plain = value_incentive(Incentive("temp_buydown", rate=5.75, structure="2-1"), P, MKT)
    for structure in ("7/6 ARM, 2-1", "5/1 ARM with 2/1 buydown", "2/1 buydown 5/1 ARM"):
        v = value_incentive(Incentive("temp_buydown", rate=5.75, structure=structure), P, MKT)
        assert (v.low, v.high, v.method) == (plain.low, plain.high, "buydown_2-1_pv"), structure
