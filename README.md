# SunMint

Tree planting registry + carbon-credit pipeline (TrueSight DAO).

## Data files — single source of truth (READ FIRST)

| File | What it is | Maintained by | Source of truth |
|---|---|---|---|
| `trees/index.geojson` | Tree points (the measurement anchors) | `scripts/build_tree_geojson.py` (workflow `rebuild-tree-index.yml`) | "SunMint Tree Planting" tab |
| `plots/index.geojson` | **Plot polygons — THE plot registry** | `scripts/build_plots_geojson.py` (workflow `rebuild-plots-index.yml`) | "SunMint Plots" tab |
| `satellite/` | Cached Sentinel-2 scenes per cell/plot | `scripts/cache_satellite_scenes.py` (workflow `cache-satellite-scenes.yml`) | Earth Search STAC (anonymous) |
| `signatures.json` | **Public auditable RSA signature ledger** — every SunMint RSA-signed event (planting, growth monitoring, planting-link, reject) as a self-verifying record | `sync_sunmint_signatures.py` (autopilot cron, every 30 min) | Telegram Chat Logs + SunMint Tree Planting + Tree Growth Measurements tabs |
| `tree_growth_measurements.json` | **Public link-share of the Tree Growth Measurements tab** — one entry per measurement (DBH/AGB/CO2e, photos, analysis SHA-256, farmer signature) | `sync_sunmint_signatures.py` (autopilot cron, every 30 min) | "Tree Growth Measurements" tab |

### ⚠️ Do NOT create or read `trees/plots.geojson`
A duplicate plot file once existed at `trees/plots.geojson` (written as a dead
side-output of the tree generator). It was empty, uncommitted, and confused
consumers — the satellite cache script read it and silently skipped plot-level
caching for real plots (RM-P1, RM-P2). **The only plot registry is
`plots/index.geojson`.** The map reads it, the cache reads it, and the plots
workflow rebuilds it from the "SunMint Plots" tab. If you see a reference to
`trees/plots.geojson` anywhere, treat it as a bug and point it at
`plots/index.geojson`.

## Public signature ledger — `signatures.json`

Every SunMint RSA-signed event is published here as a **publicly auditable,
self-verifying record**, keyed by Telegram message ID. A third party (VVB,
verifier, anyone) can independently confirm each attestation **without any
trusted intermediary**:

- **URL (stable):** `https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/signatures.json`
- **Events covered:** `[TREE PLANTING EVENT]`, `[TREE GROWTH MONITORING EVENT]`,
  `[TREE PLANTING LINK EVENT]`, `[TREE PLANTING REJECT EVENT]`
- **Each record contains:** `public_key` (base64 SPKI), `signature`
  (RSASSA-PKCS1-v1_5 over SHA-256), `signed_payload` (the exact bytes signed —
  text up to and including the `--------` separator), plus full `signed_text`,
  source tab, contributor name, and linked tree ID.
- **No PII.** Public keys, display names, and tree/geo data only — no emails,
  phones, or private keys. A fail-closed email scan runs on every build.
- Test/synthetic and malformed submissions are excluded from the public cache.

### Verify a record offline (openssl)

```bash
# 1. Fetch the ledger
curl -sL https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/signatures.json -o signatures.json
# 2. Pick a record (keyed by Telegram message ID, e.g. "171") and export:
#    public_key  -> pub.pem   (wrap in -----BEGIN PUBLIC KEY----- / -----END PUBLIC KEY-----)
#    signature   -> sig.bin   (base64 -d)
#    signed_payload -> payload.txt
# 3. Verify
openssl dgst -sha256 -verify pub.pem -signature sig.bin payload.txt
# => "Verified OK"
```

## Tree growth measurements — `tree_growth_measurements.json`

Public link-share of the (private) Tree Growth Measurements tab:

- **URL (stable):** `https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/tree_growth_measurements.json`
- **Per-measurement share URL:** append the message ID as a fragment, e.g.
  `https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/tree_growth_measurements.json#<msg_id>`
- **Each record contains:** Tree ID (QR code), Species, DBH (cm), AGB (kg),
  CO2e (kg), Lat/Lng, Measured At, close-up + context photo URLs, analysis
  commit URL + SHA-256, **farmer signature**, contributor name, status.

## Consumers
- `truesight_me_beta/sunmint.html` (impact map) → `plots/index.geojson` + `trees/index.geojson`
- `scripts/cache_satellite_scenes.py` → `trees/index.geojson` + `plots/index.geojson`
- Verifiers / VVBs / public auditors → `signatures.json` + `tree_growth_measurements.json`
