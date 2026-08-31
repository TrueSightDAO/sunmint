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
| A | Plot ID | `plot_id` | string | e.g. `RM-P1`, `SA-P1` |
| B | Farm ID | `farm_id` | string | links to agroverse.shop farm profile slug, e.g. `santa-anna-fazenda` |
| C | Plot Name | `name` | string | e.g. "Santa Anna Fazenda Plot 1 (compound)" |
| D | Hectares | `hectares` | number | declared property size |
| E | Status | `status` | string | `proposed` / `planted` / `linked` (see conventions) |
| F | Boundary Authority | `boundary_authority` | string | `approx` / `walk-approx` / `CAR-pending` / `incra` |
| G | Owner | `owner` | string | family / farmer / cooperative contact |
| H | Region | `region` | string | e.g. `Altamira, Para` |
| I | Verified At | `verified_at` | string | ISO date of the verification walk |
| J | Media | `media` | string | semicolon-separated media URLs (optional) |
| K | Notes | `notes` | string | provenance: GPS-track extents, hull area vs declared, pending evidence |
| L | Coordinates | `coordinates` | JSON string | **ring `[[lng, lat], …]` closed (first == last)** |
| M | Latitude | `lat` | number | centroid / representative point |
| N | Longitude | `lng` | number | centroid / representative point |

Output geometry: `Polygon` with one ring `[lng, lat]` (GeoJSON order).

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
column matcher, and (c) this file — then the geojson regenerated. See
`SA-P1` above as the last no-schema-change addition.
