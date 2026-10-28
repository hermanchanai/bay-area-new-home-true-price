"""validate_records rules on hand-made extractions and page text."""
from tools.validate_records import evidence_found, needs_escalation, validate

PAGE = """# SOURCE https://example.com/c
## PAGE TEXT
Quick Move-In Homes
1607 Walsh Avenue
$1,234,500
4 Beds 3 Baths 2,100 Sq. Ft.
Lot 12 - Coming Spring 2027
Limited time: 4.99% 30-year fixed rate with our preferred lender. See sales for details.
Receive up to $25,000 in flex cash toward closing costs.
"""


def home(**kw):
    h = {"address": "1607 Walsh Avenue", "lot": None, "plan_name": None, "price": 1234500, "was_price": None,
         "move_in_status": "now", "ready_date": None, "beds": 4, "baths": 3.0, "sqft": 2100,
         "evidence": "1607 Walsh Avenue $1,234,500"}
    return {**h, **kw}


def inc(**kw):
    i = {"kind": "perm_rate", "scope": "community", "home_address": None, "label": "4.99% fixed",
         "amount_usd": None, "amount_is_up_to": False, "included_in_price": False, "rate": 4.99, "apr": None,
         "structure": "30yr fixed", "lender_condition": "preferred lender", "eligibility": None, "pool_id": None,
         "expires": None, "fine_print": None, "evidence": "4.99% 30-year fixed rate with our preferred lender"}
    return {**i, **kw}


def rec(homes=(), incentives=(), confidence=0.9):
    return {"community": {"name": "C", "city": "Dublin", "status": "selling", "product": "SFD",
                          "age_restricted": False, "price_from": None, "evidence": None},
            "plans": [], "homes": list(homes), "incentives": list(incentives), "notes": None, "confidence": confidence}


def rules(issues):
    return sorted((i["where"], i["rule"]) for i in issues)


def test_clean_record_has_no_issues():
    assert validate(rec([home()], [inc()]), PAGE) == []


def test_ranges():
    bad = home(price=250000, sqft=9000, beds=0, baths=7.5, evidence="1607 Walsh Avenue")
    got = rules(validate(rec([bad]), PAGE))
    assert ("homes[0]", "price_range") in got and ("homes[0]", "sqft_range") in got
    assert ("homes[0]", "beds_range") in got and ("homes[0]", "baths_range") in got


def test_price_and_address_must_be_on_the_page():
    got = rules(validate(rec([home(address="99 Invented Way", price=1111111, evidence=None)]), PAGE))
    assert ("homes[0]", "address_not_on_page") in got and ("homes[0]", "price_not_on_page") in got


def test_quick_move_in_needs_address_or_lot_and_lot_only_is_a_warning():
    got = validate(rec([home(address=None, lot=None, evidence=None)]), PAGE)
    assert ("homes[0]", "no_address_or_lot") in rules(got)
    lot_only = validate(rec([home(address=None, lot="12", evidence="Lot 12")]), PAGE)
    assert [(i["rule"], i["severity"]) for i in lot_only] == [("lot_only", "warn")]


def test_incentive_rules():
    got = rules(validate(rec([home()], [
        inc(rate=0.0499, evidence="4.99% 30-year fixed rate"),                                   # fraction, not percent
        inc(kind="arm_promo", structure="seven year ARM", evidence="4.99% 30-year fixed rate"),
        inc(kind="flat_cash", rate=None, structure=None, amount_usd=700000, scope="home",
            home_address="1607 Walsh Avenue", evidence="Receive up to $25,000 in flex cash"),       # > 50% of price
    ]), PAGE))
    assert ("incentives[0]", "rate_range") in got
    assert ("incentives[1]", "arm_structure") in got
    assert ("incentives[2]", "amount_over_50pct") in got


def test_big_real_cuts_are_only_a_warning():
    # Bay Area promos run past 25%: "$300,000 in savings" on a $789,925 home (38%)
    got = validate(rec([home()], [inc(kind="flat_cash", rate=None, structure=None, amount_usd=400000,
                                      evidence="Receive up to $25,000 in flex cash")]), PAGE)
    assert [(i["rule"], i["severity"]) for i in got] == [("amount_over_25pct", "warn")]


def test_evidence_must_match_page_text():
    assert evidence_found("Receive up to $25,000 in flex cash toward closing costs", PAGE)
    assert evidence_found("receive up to $25,000 in flex-cash toward closing cost", PAGE)  # small drift is fine
    assert not evidence_found("Receive up to $75,000 in builder bucks for upgrades and more", PAGE)
    # the model often stitches a card's lines into one quote; every token is still on the page, close together
    assert evidence_found("1607 Walsh Avenue $1,234,500 Lot 12", PAGE)
    assert not evidence_found("1607 Walsh Avenue $1,234,999 Lot 12", PAGE)
    got = validate(rec([home()], [inc(evidence="5.25% fixed for the life of the loan, no lender required")]), PAGE)
    assert rules(got) == [("incentives[0]", "evidence_not_on_page")]


def test_needs_escalation():
    assert not needs_escalation(rec(), [])
    assert needs_escalation(rec(confidence=0.6), [])
    assert needs_escalation(rec(), [{"where": "homes[0]", "rule": "price_range", "severity": "error"}])
    assert not needs_escalation(rec(), [{"where": "homes[0]", "rule": "lot_only", "severity": "warn"}])


def test_stitched_quote_across_a_photo_carousel():
    # Pulte puts the "$100,000 in Savings" badge ~70 tokens (10 photo captions) above the address
    captions = "\n".join(f"Photo caption {n} with a few words" for n in range(10))
    page = f"$100,000 in Savings\n{captions}\nOxford\n1486 PRAIRIE OAK AVE\nMANTECA, CA 95337\n$890,316\n"
    assert evidence_found("$100,000 in Savings Oxford 1486 PRAIRIE OAK AVE", page)


def test_home_quote_drift_is_a_warning_when_address_and_price_check_out():
    # homes are verified field by field (address, price on the page); a paraphrased quote isn't a fabrication
    got = validate(rec([home(evidence="1607 Walsh Avenue $1,234,500 Available Now")]), PAGE)
    assert [(i["rule"], i["severity"]) for i in got] == [("evidence_not_on_page", "warn")]


def test_price_cut_size_is_judged_against_the_original_price():
    # Century: "was $1,236,615 now $779,000" -> a $457,615 cut is 37% of the original price, not 59% of the new one
    page = PAGE + "Was $1,236,615 now $779,000\n"
    cut = inc(kind="price_cut", rate=None, structure=None, amount_usd=457615, included_in_price=True,
              evidence="Was $1,236,615 now $779,000")
    got = validate({**rec([home()], [cut]), "community": {"price_from": 779000}}, page)
    assert [(i["rule"], i["severity"]) for i in got] == [("amount_over_25pct", "warn")]
