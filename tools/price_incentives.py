"""Turn builder incentives into dollars and a true-price range. Pure math, no I/O.

Rates are annual percentages (4.99 means 4.99%), the way builders and FRED print them.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Assumptions:
    down_payment_pct: float = 0.20
    term_months: int = 360
    closing_cost_pct: float = 0.03
    jumbo_spread_pct: float = 0.25
    conforming_limit: int | None = None
    unstated_term_years: int = 5     # a rate with no stated term counts this long: the shortest ARM period builders here use


@dataclass(frozen=True)
class Incentive:
    kind: str
    amount_usd: float | None = None
    amount_is_up_to: bool = False
    rate: float | None = None
    note_rate: float | None = None
    structure: str | None = None
    pool_id: str | None = None
    label: str = ""


@dataclass(frozen=True)
class Value:
    low: int
    high: int
    method: str
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TruePrice:
    asking: int
    value_low: int
    value_high: int
    true_low: int
    true_high: int
    counted: tuple[tuple[Incentive, Value], ...]
    not_counted: tuple[tuple[Incentive, Value], ...]
    market_rate: float


TERM_NOT_STATED = "term not stated"
RATE_KINDS = {"perm_rate", "temp_buydown", "arm_promo", "first_year_rate"}
CASH_KINDS = {"flat_cash", "price_cut", "closing_credit"}


def monthly_payment(rate_pct: float, loan: float, months: int = 360) -> float:
    i = rate_pct / 100 / 12
    if i == 0:
        return loan / months
    return loan * i / (1 - (1 + i) ** -months)


def comparison_rate(price: float, market_rate_pct: float, a: Assumptions) -> float:
    """Market rate the buyer would otherwise pay: jumbo loans pay the jumbo spread on top."""
    loan = price * (1 - a.down_payment_pct)
    if a.conforming_limit is not None and loan > a.conforming_limit:
        return market_rate_pct + a.jumbo_spread_pct
    return market_rate_pct


def _pv(monthly_savings: list[float], discount_rate_pct: float) -> float:
    d = 1 + discount_rate_pct / 100 / 12
    return sum(s / d ** k for k, s in enumerate(monthly_savings, start=1))


def _ints(text: str | None) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", text or "")]


def value_incentive(inc: Incentive, price: float, market_rate_pct: float, a: Assumptions = Assumptions()) -> Value:
    """Dollar value of one incentive as a (low, high) range. Never raises on bad input: flags it."""
    if inc.kind == "unquantified":
        return Value(0, 0, "unquantified", ("unpriced",))

    if inc.kind in CASH_KINDS:
        if inc.amount_usd is None:
            return Value(0, 0, inc.kind, ("missing_input",))
        high = inc.amount_usd
        if inc.kind == "closing_credit" and inc.amount_is_up_to:
            high = min(high, a.closing_cost_pct * price)
        low = 0 if inc.amount_is_up_to else high
        return Value(round(low), round(high), "face_value")

    if inc.kind not in RATE_KINDS:
        return Value(0, 0, inc.kind, ("unknown_kind",))
    if inc.rate is None:
        return Value(0, 0, inc.kind, ("missing_input",))

    market = comparison_rate(price, market_rate_pct, a)
    flags = ("jumbo",) if market != market_rate_pct else ()
    loan = price * (1 - a.down_payment_pct)
    n = a.term_months

    if inc.kind in ("temp_buydown", "first_year_rate"):
        if inc.kind == "first_year_rate":
            steps, flags = [1], flags + ("assumed_1-0",)
        else:
            steps = [s for s in _ints(inc.structure) if s > 0]
            if not steps:
                return Value(0, 0, inc.kind, flags + ("missing_input",))
        note = inc.note_rate if inc.note_rate is not None else inc.rate + steps[0]
        full = monthly_payment(note, loan, n)
        savings = []
        for step in steps:
            savings += [full - monthly_payment(note - step, loan, n)] * 12
        v = round(_pv(savings, market))
        return Value(v, v, f"buydown_{'-'.join(map(str, steps))}_pv", flags)

    if inc.rate >= market:
        return Value(0, 0, inc.kind, flags + ("promo_not_below_market",))
    saving = monthly_payment(market, loan, n) - monthly_payment(inc.rate, loan, n)

    if inc.kind == "arm_promo":
        years = _ints(inc.structure)
        if not years:
            return Value(0, 0, inc.kind, flags + ("missing_input",))
        v = round(_pv([saving] * years[0] * 12, market))
        return Value(v, v, f"arm_fixed_{years[0]}yr_pv", flags)

    if inc.structure == TERM_NOT_STATED:
        v = round(_pv([saving] * a.unstated_term_years * 12, market))
        return Value(v, v, f"term_unstated_{a.unstated_term_years}yr_pv", flags)

    # perm_rate: buyer view = price cut with the same payment at market; builder view = PV of savings.
    payment_equiv_cut = price * (1 - monthly_payment(inc.rate, loan, n) / monthly_payment(market, loan, n))
    builder_cost = _pv([saving] * n, market)
    return Value(round(builder_cost), round(payment_equiv_cut), "perm_builder_cost..payment_equiv_cut", flags)


def true_price(price: float, incentives: list[Incentive], market_rate_pct: float,
               a: Assumptions = Assumptions()) -> TruePrice:
    """Asking minus incentives. Incentives sharing a pool_id are one choice: the buyer takes the best, so
    the pool is worth its best guaranteed value (floor) up to its best possible value (ceiling)."""
    valued = [(inc, value_incentive(inc, price, market_rate_pct, a)) for inc in incentives]
    pools: dict[object, tuple[Incentive, Value]] = {}
    floors: dict[object, int] = {}
    for idx, (inc, v) in enumerate(valued):
        key = inc.pool_id if inc.pool_id is not None else ("solo", idx)
        if key not in pools or v.high > pools[key][1].high:
            pools[key] = (inc, v)
        floors[key] = max(floors.get(key, 0), v.low)
    counted = tuple(pools.values())
    counted_ids = {id(inc) for inc, _ in counted}
    not_counted = tuple((inc, v) for inc, v in valued if id(inc) not in counted_ids)
    lo = sum(floors.values())
    hi = sum(v.high for _, v in counted)
    return TruePrice(asking=round(price), value_low=lo, value_high=hi,
                     true_low=round(price - hi), true_high=round(price - lo),
                     counted=counted, not_counted=not_counted,
                     market_rate=comparison_rate(price, market_rate_pct, a))
