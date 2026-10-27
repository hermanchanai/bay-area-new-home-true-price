"""Fixtures are the attribute dicts of single public parcels, copied from the county layers on
2026-10-27 (the same parcels the probes checked)."""
from tools import db
from tools.pull_assessor import (clean, epoch_ms_to_date, norm_alameda, norm_contra_costa,
                                 norm_san_joaquin, norm_transfers, ring_centroid, to_int,
                                 upsert_parcels, upsert_transfers, yyyymmdd_to_date)

AL = {'APN': '900-00-001', 'SitusAddress': '100 EXAMPLE CT DUBLIN 94568', 'SitusCity': 'DUBLIN',
      'Land': 400000, 'Imps': 1000000, 'LatestDocumentDate': 1681776000000, 'LatestDocument_Prefix': '2023',
      'LatestDocumentSeries': '000123', 'UseCode': '1800'}
CC = {'APN': '000000001', 'full_address_display': '200 SAMPLE DR   , BRENTWOOD', 's_city': 'BRENTWOOD',
      'LAND_VALUE': 250000, 'IMP_VAL': 850000, 'C_DED_DT': '20240119', 'C_DED_NBR': '24000001',
      'USE_CODE': 11, 'YR_BUILT': None}
SJ = {'APN': '00000001', 'FULL_ADDRESS': '300 W TEST ST MOUNTAIN HOUSE CA 95391', 'SITUSCITY': 'MH',
      'LAND_VALUE': 200000, 'IMPROVEMENT_VALUE': 500000, 'YEAR_BUILT': '2024', 'VALUE_ROLL_YEAR': '2026',
      'USECODE': '10', 'BEDROOMS': 4, 'TOTALLIV_AREA': 1938}
T = {'apn': '900-00-001     ', 'doc_dt': 1681776000000, 'doc_prefix': '2023', 'doc_series': '123',
     'value_from_trans_tax': ' 000001250000.', 'doc_parcel_count': '1', 'name_type': 'TRANSFEREE'}


def test_helpers():
    assert clean('200 SAMPLE DR   , BRENTWOOD') == '200 SAMPLE DR, BRENTWOOD'
    assert clean('   ') is None
    assert epoch_ms_to_date(1681776000000) == '2023-04-18'
    assert yyyymmdd_to_date('20240119') == '2024-01-19'
    assert yyyymmdd_to_date('00000000') is None and yyyymmdd_to_date('2024') is None
    assert to_int(' 000001250000.') == 1_250_000 and to_int('              ') is None
    assert to_int(' 2024') == 2024 and to_int(500000) == 500000


def test_ring_centroid_drops_closing_point():
    sq = {"rings": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    assert ring_centroid(sq) == (1.0, 1.0)
    assert ring_centroid({}) is None


def test_normalizers():
    a = norm_alameda(AL, (37.71, -121.90))
    assert (a["total"], a["deed_date"], a["deed_doc"], a["city"]) == (1_400_000, '2023-04-18', '2023-000123', 'DUBLIN')
    c = norm_contra_costa(CC, None)
    assert (c["total"], c["deed_date"], c["address"], c["use_code"]) == (1_100_000, '2024-01-19', '200 SAMPLE DR, BRENTWOOD', '11')
    s = norm_san_joaquin(SJ, None)
    assert (s["total"], s["year_built"], s["roll_year"], s["beds"], s["living_sqft"]) == (700_000, 2024, 2026, 4, 1938)


def test_transfers_dedupe_buyers_and_drop_multi_parcel_price():
    rows = norm_transfers([T, dict(T)])  # two buyers on one deed
    assert len(rows) == 1 and rows[0]["price"] == 1_250_000 and rows[0]["doc_id"] == '2023-123'
    multi = norm_transfers([{**T, 'apn': '1-2-3', 'doc_parcel_count': '12'}])
    assert multi[0]["price"] is None and multi[0]["parcel_count"] == 12
    blank = norm_transfers([{**T, 'value_from_trans_tax': '              '}])
    assert blank[0]["price"] is None


def test_upserts_round_trip():
    conn = db.connect(":memory:")
    upsert_parcels(conn, [norm_alameda(AL, (37.71, -121.90)), norm_san_joaquin(SJ, None)])
    upsert_parcels(conn, [norm_alameda(AL, (37.71, -121.90))])  # re-pull replaces, not duplicates
    assert conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0] == 2
    upsert_transfers(conn, norm_transfers([T]))
    assert conn.execute("SELECT price FROM transfers").fetchone()[0] == 1_250_000
