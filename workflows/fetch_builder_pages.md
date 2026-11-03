# Workflow: fetch builder pages

**Objective:** save each builder's community pages for the counties in `config/cities.yaml` to `.tmp/raw/<date>/<builder>/`
(gitignored; never committed), so extraction can run, and re-run, without touching the sites again.

**Run:** `.venv/bin/python -m tools.fetch_builder_pages --builder all`, or a builder key from
`config/builders.yaml`. Exits 1 if any builder falls below its floor.

**How discovery works:** start page → (optional) middle pages → community paths found
by regex in the raw page text (embedded JSON included) → keep only cities listed in
`config/cities.yaml` → fetch each community page, plus any `extra_pages`.
- Middle pages (`city_pattern`) are city pages (Pulte, Century) or master-plan pages (Lennar's River
  Islands, Tracy Hills ...), keyed by path because one city can hold several master plans.
- A regex `path` group picks the URL out of a longer match (KB's JSON-LD: URL and city are apart).
  City names are turned into slugs ("Mountain House" → `mountain-house`).
- `city_from_page: true` (Toll, Meritage, Tri Pointe, Signature): the URL doesn't name the city, so each
  candidate page is fetched and its own address decides (JSON-LD `addressLocality`, or
  `page_city_pattern`). Out-of-scope pages are deleted and logged as kind `out_of_scope`.
- `enabled: false` + `skip_reason`: builders whose pages carry no data without running the site's
  JavaScript (FCB, Richmond American) or that show a bot challenge (D.R. Horton). Skipped, never
  worked around.

**Politeness (non-negotiable)**
- robots.txt is read with our own User-Agent. 4xx means no robots file, so allow; 5xx means disallow
  everything. Taylor Morrison 403s the default Python User-Agent, which made Python's own robots
  reader block the whole site.
- 2 s between requests to one host. No retries past a block.
- Never solve a CAPTCHA or bot challenge. A blocked builder is logged and skipped.

**Floor:** fetched communities must be ≥ 60% of `expected_communities` (from the 2026-10-26 roster).

**Results (2026-10-27):** zero errors and zero robots refusals.

| Builder | Communities fetched | Roster count | Not on the roster |
|---|---:|---:|---|
| Taylor Morrison | 11 | 6 | Pittsburg ×2, Manteca ×2, Newark |
| Pulte | 12 | 11 | The Square, Sunnyvale |
| Trumark | 18 | 16 | The Trails (San Ramon), Woodlands (Walnut Creek) |

Some of these may be sold out or coming soon; extraction records their status.

**Adding a builder:** add an entry to `config/builders.yaml` (start page, patterns, expected count),
then run with `--builder <key>` and check the manifest.
