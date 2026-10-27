# Workflow: pull assessor parcels

**Objective:** load every single-family parcel built or sold since 2022 in the three counties with
free assessor data, plus Alameda's recorded sale prices, for the "what earlier buyers paid" step.

**Run:** `.venv/bin/python -m tools.pull_assessor --county all` (about 10 minutes, ~430 requests).
Logs go to `.tmp/logs/`. Re-running replaces rows; it never duplicates them.

**What it pulls** (where clauses live in `config/counties.yaml`):

| County | Layer | Universe | Gives |
|---|---|---|---|
| Alameda | Parcels FeatureServer | UseCode 1100/1800, latest document ≥ 2022 | value, deed date + doc id, centroid |
| Alameda | Ownership Transfer List | transferee rows, SFR/PUD | sale price from transfer tax, 4/2023–3/2025 |
| Contra Costa | Assessment_Parcels_ArcPro MapServer | USE_CODE 11, deed ≥ 2022-01-01 | value, deed date + doc no., centroid from the ring |
| San Joaquin | Parcels FeatureServer | USECODE 10, year built ≥ 2022 | value, year built, roll year, beds, living sq ft |

Santa Clara has no free source (status `red`): the tool refuses it.

**Checks built in:** the fetched row count must equal the server's `returnCountOnly`, or the run
fails.

**Results (2026-10-27):**
- Alameda: 80,524 rows, 80,364 unique APNs; 41,603 transfers, 14,520 with a single-parcel price.
- Contra Costa: 61,689 rows, 61,688 unique APNs.
- San Joaquin: 12,951 rows.

**Edge cases**
- Alameda's transfer list has one row per buyer: rows are collapsed per (APN, document date).
- A document covering several parcels carries the price of all of them, so it keeps the sale and drops
  the price.
- Contra Costa's `YR_BUILT` is empty; new tracts are found by deed-date clusters (linking step).
- San Joaquin has no deed date in the layer; the per-APN lookup on `common1.mptsweb.com` adds it
  later, only for linked parcels, slowly (its terms are unread).
- Duplicate APNs within a layer (split polygons) collapse to one row on upsert.
