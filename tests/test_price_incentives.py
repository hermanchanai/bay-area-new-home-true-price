"""Expected values were computed by hand (a separate scratch calculation on 2026-10-26, written
before this module existed), not read back from the implementation. Base case: $1,000,000 price,
20% down, 30-yr amortization, market rate 6.25%, savings discounted monthly at the market rate."""
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


def test_permanent_rate_buydown_two_views():
    v = value_incentive(Incentive("perm_rate", rate=4.99, structure="30yr fixed"), P, MKT)
    assert v.high == pytest.approx(129_128, abs=1)  # payment-equivalent price cut
    assert v.low == pytest.approx(103_303, abs=1)   # builder cost, PV over 360 months


def test_two_one_buydown():
    v = value_incentive(Incentive("temp_buydown", rate=4.625, structure="2-1"), P, MKT)
    assert v.low == v.high == pytest.approx(17_351, abs=1)


def test_three_two_one_buydown():
    v = value_incentive(Incentive("temp_buydown", rate=3.5, structure="3-2-1"), P, MKT)
    assert v.low == v.high == pytest.approx(33_193, abs=1)


def test_arm_promo_counts_only_fixed_period():
    v = value_incentive(Incentive("arm_promo", rate=4.99, structure="7/6 ARM"), P, MKT)
    assert v.low == v.high == pytest.approx(43_185, abs=1)


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
    assert v.high == pytest.approx(303_314, abs=1)
    assert v.low == pytest.approx(242_651, abs=1)


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
    assert tp.value_high == pytest.approx(129_128 + 2_500, abs=1)
    assert tp.value_low == pytest.approx(103_303 + 2_500, abs=1)
    assert tp.true_low == pytest.approx(P - 129_128 - 2_500, abs=1)
    assert tp.true_high == pytest.approx(P - 103_303 - 2_500, abs=1)
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
