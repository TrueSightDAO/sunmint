# SunMint — Data Schema

Canonical schema documentation for the SunMint registries (TrueSight DAO).
Companion to `README.md`. Org convention: see also `tokenomics/SCHEMA.md`,
`lineage-assets/SCHEMA.md`.

## Registry map

| Registry | File | Geometry | Source of truth (sheet) | Generator |
|---|---|---|---|---|
| Plots | `plots/index.geojson` | Polygon | `SunMint Plots` tab (spreadsheet `1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ`) | `scripts/build_plots_geojson.py` (workflow `rebuild-plots-index.yml`) |
| Trees | `trees/index.geojson` | Point | `SunMint Tree Planting` tab (same spreadsheet) | `scripts/build_tree_geojson.py` (workflow `rebuild-tree-index.yml`) |
| Satellite | `satellite/` | Scene rasters | Earth Search STAC (anonymous) | `scripts/cache_satellite_scenes.py` |

> ⚠️ The **only** plot registry is `plots/index.geojson`. Do NOT create/read
> `trees/plots.geojson` (a dead duplicate that once confused consumers).

## Plots schema (`SunMint Plots` tab → `plots/index.geojson`)

Columns (canonical names; the generator matches headers flexibly, most
specific first):

| # | Column | geojson property | Type | Notes |
|---|---|---|---|---|
| A | Plot ID | `plot_id` | string | unique plot id (e.g. `RM-P1`) |
| B | Farm ID | `farm_id` | string | farm slug (e.g. `rancho-maranta`) |
| C | Plot Name | `name` | string | human-readable name |
| D | Hectares | `hectares` | number | plot area |
| E | Status | `status` | string | `proposed` / `planted` / `verified` (see conventions) |
| F | Boundary Authority | `boundary_authority` | string | `approx` / `walk-approx` / `CAR-pending` / `incra` |
| G | Plot Type | `plot_type` | string | **program role** — `restoration` / `mature` / `maturing` / `enrichment` / `research` / `nursery` / `infrastructure` (blank = unclassified; see conventions) |
| H | Owner | `owner` | string | family / farmer / cooperative contact |
| I | Region | `region` | string | e.g. `Altamira, Para` |
| J | Verified At | `verified_at` | string | ISO date of the verification walk |
| K | Media | `media` | string | semicolon-separated media URLs (optional) |
| L | Notes | `notes` | string | provenance: GPS-track extents, hull area vs declared, pending evidence |
| M | Coordinates | `coordinates` | JSON string | **ring `[[lng, lat], …]` closed (first == last)** |
| N | Latitude | `lat` | number | centroid / representative point |
| O | Longitude | `lng` | number | centroid / representative point |
| P–S | Invalidated By / (blank) / At / Reason | — | string | **not read by the generator** — retraction bookkeeping written by the GAS/DApp |

Output geometry: `Polygon` with one ring `[lng, lat]` (GeoJSON order).

> The `#` column letters above are **indicative only** — the sheet's physical order drifts
> (`Plot Type` now sits at G, with `Invalidated *` at P–S). The generator always matches by
> header **name**, never by position, so a column can be moved without any code change.

### Status conventions
- `proposed` — boundary approximate, evidence pending (e.g. awaiting boundary
  photos / CAR polygon)
- `planted` — verified planting on the plot (e.g. RM-P1)
- `linked` — plot linked to registered trees/QRs

### Boundary-authority conventions
- `approx` — hull/approx polygon from GPS track; not authoritative
- `walk-approx` — derived from a documented GPS walk (state timestamps + clip
  counts in `notes`)
- `CAR-pending` — farm's CAR (Cadastro Ambiental Rural) polygon requested but
  not yet received

### Plot-type conventions
`plot_type` records a plot's **role in the program** — the axis the impact map filters on, and
the axis the tree-count estimator keys on (mature / enrichment / research plots are never
auto-estimated). It is orthogonal to `status` (lifecycle) and `boundary_authority` (evidence grade).

- `restoration` — net-new planting on a prior **non-forest** baseline (pasture / cleared land).
  Carries the additionality case; do **not** apply to a plot that was already forest/agroforest.
- `mature` — established cacao/agroforest the farmer already had (incl. cabruca, century-old groves).
- `maturing` — established-but-still-growing stand: canopy filling in / bearing, not yet a
  closed old grove. The **walk-observed middle stage** between a young planting and `mature`.
- `enrichment` — **additional** trees planted into an existing stand.
- `research` — research / trial plot; excluded from headline sequestration & 10,000-ha counts.
- `nursery` — seedling production.
- `infrastructure` — non-crop built area (processing yard, drying terrace, fermentary, compound).
- *(blank)* — **not yet classified.** Never auto-defaulted by the generator or any writer.

> **The sheet column is a dropdown.** Live `SunMint Plots!G` carries a strict `ONE_OF_LIST`
> data-validation rule with exactly these seven values (added 2026-09, thread 24326), so a
> hand-edit in the sheet can't introduce an off-vocabulary token. Programmatic writes (GAS
> `setValue`, DApp submissions) are **not** blocked by validation — the GAS logs a
> non-fatal warning on an off-vocabulary value as the guard on that path.

> **Mutability:** `plot_type` is a **current-state** attribute and can change (a `restoration`
> plot passes through `maturing` to `mature` in ~15 yr). The *immutable* "was this land forest before?" fact belongs
> to the carbon/additionality annex (CAR / satellite-fed), not to this column. A plot tagged
> `restoration` is *claiming* that additionality at enrollment.

> The generator **warns** (does not reject) on an unrecognized `plot_type` and on any schema
> field whose sheet header is missing — so a tag cannot silently disappear from the registry.

### Worked example — SA-P1 (2026-08-31)
Santa Anna Fazenda (Pará, CEPOTX member, introduced by Jedielcio). 3 ha
declared; GPS track from 44 media (32 HEIC + 12 MOV, 15:28–15:58) yields an
~0.31 ha compound hull → `status: proposed`, `boundary_authority: approx`,
notes flag the full 3 ha boundary pending Jedielcio's boundary photos (email
preserves EXIF; WhatsApp/Telegram strip it).

## Trees schema (`SunMint Tree Planting` tab → `trees/index.geojson`)

Columns matched by the generator (exact-match, most specific first):

| Column | geojson property | Type | Notes |
|---|---|---|---|
| Telegram Update ID / Tree ID | `tree_id` | string | e.g. `Edgar_20260821175134_005` |
| Specie / Species | `species` | string | e.g. `Bougainvillea` |
| Latitude | — | number | point geometry y |
| Longitude | — | number | point geometry x |
| Photo of Tree Planted | `photo_url` | string | github.com URL (rewritten to raw) |
| Status | `status` | string | e.g. `LINKED` |
| Linked QR Code | `qr_code` | string | e.g. `FOUNDERHAUS_BOUGAINVILLEA_20260821_1` |
| Tree Planting Time | `last_measured` | string | ISO timestamp |
| Plot ID | `plot_id` | string | optional link back to plots registry |

Output geometry: `Point` `[lng, lat]`.

Rejected/invalid trees remain in the sheet as audit history but are excluded
from the geojson.

## Consumers
- `truesight_me_beta/sunmint.html` (impact map, beta.truesight.me/sunmint.html)
  → `plots/index.geojson` (jsDelivr primary, raw.githubusercontent fallback,
  cache-busted) + `trees/index.geojson`
- `scripts/cache_satellite_scenes.py` → both registries (plot-level caching
  reads `plots/index.geojson` only)

## Extending the schema
New plot/tree columns must be added to (a) the sheet tab, (b) the generator's
column matcher, and (c) this file — then the geojson regenerated. `Plot Type` (`plot_type`) is a
**single** column (7 values, incl. `maturing`) — do **not** split stage into a second column; the
last no-schema-change addition was `SA-P1` above.
