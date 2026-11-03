# Workflow: pull assessor parcels

**Objective:** load every single-family parcel built or sold since 2022 in the three counties with
free assessor data, plus Alameda's recorded sale prices, for the county records of earlier homes.

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

## Linking parcels to communities and building county records (added 2026-10-27, records rule 2026-11-03)

**Run:** `.venv/bin/python -m tools.link_parcels --spot-check` (about 2 minutes; live book-page counts are
cached in `.tmp/link/`). Writes `community_parcels`, `past_records`, `past_sales`, community lat/lng, and
`eval/reports/<date>__past_sales.md`.

**Linking, in order** (each rule came from a wrong link found on real data):
1. Streets come from the community's home addresses (number + street name, suffix dropped).
2. Centre = the 900 m cluster of county parcels holding the most of those streets, within 8 km of the
   community's city. The city is matched loosely because builders use marketing cities (Brooks at
   Creekside says Antioch; same-named streets in Oakley are 11.8 km from its own page pin).
3. Parcels within 900 m on those streets are linked (exact address 1.0, street 0.8).
4. Alameda and Contra Costa: a book-page where under 60% of single-family parcels sold since 2022 is an
   older neighbourhood and is dropped (live `returnCountOnly` per book-page).
5. San Joaquin: parcels more than 25% outside the community's home sizes are another collection on a
   shared street (Regency at Tracy Lakes' Pinecrest vs. its larger collections on Lakeshore Av).
6. When the community's own page has a map pin (one not shared by 3+ of the builder's pages, which is a
   market centre), the cluster must be within 2 km of it.

**Records, never estimates:** each linked home shows the assessor's full assessed value as published, plus
Alameda's recorded transfer price where there is one. A value counts once it reflects the last sale: deed on or
before the roll's `reassessed_through` date (Alameda, Contra Costa), or built before the roll year (San Joaquin, which
publishes no deed dates). Not shown: improvements 0, a deed shared by several parcels (a bulk lot transfer to the
builder), a deed before the year built. A value under 60% of the community's median per sq ft is withheld as possibly
part-built, and the page says how many. A community with any record lists them; the median needs five values.
`rebuild_records` rebuilds both tables from the stored links with no county requests.

**Edge cases**
- Alameda's transfer list has one row per buyer: rows are collapsed per (APN, document date).
- A document covering several parcels carries the price of all of them, so it keeps the sale and drops
  the price.
- Contra Costa's `YR_BUILT` is empty; new tracts are found by deed-date clusters (linking step).
- San Joaquin has no deed date in the layer; the per-APN lookup on `common1.mptsweb.com` adds it
  later, only for linked parcels, slowly (its terms are unread).
- Duplicate APNs within a layer (split polygons) collapse to one row on upsert.

## Linking parcels to communities and building county records (added 2026-10-27, records rule 2026-11-03)

**Run:** `.venv/bin/python -m tools.link_parcels --spot-check` (about 2 minutes; live book-page counts are
cached in `.tmp/link/`). Writes `community_parcels`, `past_records`, `past_sales`, community lat/lng, and
`eval/reports/<date>__past_sales.md`.

**Linking, in order** (each rule came from a wrong link found on real data):
1. Streets come from the community's home addresses (number + street name, suffix dropped).
2. Centre = the 900 m cluster of county parcels holding the most of those streets, within 8 km of the
   community's city. The city is matched loosely because builders use marketing cities (Brooks at
   Creekside says Antioch; same-named streets in Oakley are 11.8 km from its own page pin).
3. Parcels within 900 m on those streets are linked (exact address 1.0, street 0.8).
4. Alameda and Contra Costa: a book-page where under 60% of single-family parcels sold since 2022 is an
   older neighbourhood and is dropped (live `returnCountOnly` per book-page).
5. San Joaquin: parcels more than 25% outside the community's home sizes are another collection on a
   shared street (Regency at Tracy Lakes' Pinecrest vs. its larger collections on Lakeshore Av).
6. When the community's own page has a map pin (one not shared by 3+ of the builder's pages, which is a
   market centre), the cluster must be within 2 km of it.

**Sale estimate:** Alameda transfer-tax price where present; otherwise value ÷ 1.02 per July-1 roll since
the sale (Alameda/Contra Costa 2025-26 roll, San Joaquin 2026-27 with the sale assumed in the year
built, so estimates there may run up to 2% high). Not estimated: improvements 0, a deed shared by several
parcels (a bulk lot transfer to the builder), deeds in 2026 (not reassessed yet), a deed before the year
built. Five estimates minimum per community.

**Edge cases**
- Alameda's transfer list ends 3/2025; its communities selling now mostly have no buyer deeds yet, so the
  transfer-tax check samples the list itself.
- Communities whose homes have no address, or whose streets are not on the roll yet, show no records.
- Townhome use codes are outside the pulled universe (Alameda 7300/7305, Contra Costa non-11).
