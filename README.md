# SunMint

Tree planting registry + carbon-credit pipeline (TrueSight DAO).

## Data files — single source of truth (READ FIRST)

| File | What it is | Maintained by | Source of truth |
|---|---|---|---|
| `trees/index.geojson` | Tree points (the measurement anchors) | `scripts/build_tree_geojson.py` (workflow `rebuild-tree-index.yml`) | "SunMint Tree Planting" tab |
| `plots/index.geojson` | **Plot polygons — THE plot registry** | `scripts/build_plots_geojson.py` (workflow `rebuild-plots-index.yml`) | "SunMint Plots" tab |
| `satellite/` | Cached Sentinel-2 scenes per cell/plot | `scripts/cache_satellite_scenes.py` (workflow `cache-satellite-scenes.yml`) | Earth Search STAC (anonymous) |

### ⚠️ Do NOT create or read `trees/plots.geojson`
A duplicate plot file once existed at `trees/plots.geojson` (written as a dead
side-output of the tree generator). It was empty, uncommitted, and confused
consumers — the satellite cache script read it and silently skipped plot-level
caching for real plots (RM-P1, RM-P2). **The only plot registry is
`plots/index.geojson`.** The map reads it, the cache reads it, and the plots
workflow rebuilds it from the "SunMint Plots" tab. If you see a reference to
`trees/plots.geojson` anywhere, treat it as a bug and point it at
`plots/index.geojson`.

## Consumers
- `truesight_me_beta/sunmint.html` (impact map) → `plots/index.geojson` + `trees/index.geojson`
- `scripts/cache_satellite_scenes.py` → `trees/index.geojson` + `plots/index.geojson`
