"""Build the SunMint plots registry (plots/index.geojson).

Mirrors scripts/build_tree_geojson.py. Reads the "SunMint Plots" tab of the
SunMint ledger spreadsheet and regenerates plots/index.geojson.

SAFETY: if the tab does not exist or has no rows, PRESERVE the existing
plots/index.geojson (the curated seed: RM-P1, RM-P2, ...) instead of clobbering
it with an empty FeatureCollection -- an empty file would blank the impact-map
polygons that already render.

Usage:
  python3 scripts/build_plots_geojson.py [--out plots/index.geojson]
"""

import argparse
import datetime
import json
import os
import sys

SHEET_ID = "1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ"
SHEET_TAB = "SunMint Plots"

# Field -> accepted column names (most specific first, exact-before-prefix).
# Mirrors build_tree_geojson.idx() behaviour.
FIELD_COLUMNS = {
    "plot_id": ["plot id", "plot"],
    "farm_id": ["farm id", "farm"],
    "name": ["plot name", "name", "site name"],
    "hectares": ["hectares", "area ha", "area"],
    "status": ["status"],
    # plot_type: controlled vocabulary (see VALID_PLOT_TYPES below). Header is 'Plot Type' --
    # named to avoid a 'farm'/'plot' prefix collision with farm_id/plot_id in idx().
    "plot_type": ["plot type", "type"],
    "boundary_authority": ["boundary authority", "authority"],
    "owner": ["owner", "family", "farmer"],
    "region": ["region", "state", "municipality"],
    "verified_at": ["verified at", "verified date"],
    "media": ["media", "media urls", "photo urls"],
    "notes": ["notes", "comments"],
    "coordinates": ["coordinates", "polygon", "coords", "geometry"],
    "lat": ["latitude"],
    "lng": ["longitude"],
}

# Controlled vocabulary for the `plot_type` column (see SUNMINT_PLOTS_REGISTRY.md sec 5).
# Blank = unclassified and is NEVER auto-defaulted: classifying a plot's carbon eligibility
# by guess is how you end up crediting a baseline you never established.
#   restoration    - net-new planting on a prior non-forest baseline (pasture / cleared land)
#   mature         - established cacao/agroforest the farmer already had (incl. cabruca)
#   maturing       - established-but-still-growing stand: canopy filling in / bearing, not yet a
#                    closed old grove (walk-observed middle stage; the sheet is authoritative)
#   enrichment     - additional trees planted into an existing stand
#   research       - research / trial plot (excluded from headline sequestration counts)
#   nursery        - seedling production
#   infrastructure - non-crop built area (processing yard / drying terrace / compound)
VALID_PLOT_TYPES = {
    "restoration",
    "mature",
    "maturing",
    "enrichment",
    "research",
    "nursery",
    "infrastructure",
}


def get_sheet():
    import gspread
    from google.oauth2 import service_account

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON env var required")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)


def idx(header, names):
    hl = [(h or "").strip().lower() for h in header]
    for n in names:
        n = n.strip().lower()
        if not n:
            continue
        for i, h in enumerate(hl):
            if h == n:
                return i
        for i, h in enumerate(hl):
            if h.startswith(n):
                return i
    return None


def cell(row, i):
    try:
        v = row[i].strip() if i is not None and i < len(row) else ""
    except Exception:
        v = ""
    return v or None


def to_float(v):
    try:
        return float(v.replace(",", ".")) if v else None
    except (ValueError, AttributeError):
        return None


def parse_coordinates(raw, lat, lng, hectares):
    """Return a closed GeoJSON ring, or None."""
    ring = None
    if raw:
        try:
            ring = json.loads(raw)
        except (ValueError, TypeError):
            ring = None
    # Fallback: lat/lng centre + hectares -> simple square (approx authority).
    if ring is None and lat is not None and lng is not None:
        try:
            ha = float(hectares) if hectares else 1.0
        except (ValueError, TypeError):
            ha = 1.0
        side = (ha**0.5) / 111.0  # degrees lat; lng scaled by cos
        lng_scale = max(0.1, __import__("math").cos(__import__("math").radians(lat)))
        d = side / lng_scale
        ring = [
            [lng - d, lat - side / 2],
            [lng + d, lat - side / 2],
            [lng + d, lat + side / 2],
            [lng - d, lat + side / 2],
            [lng - d, lat - side / 2],
        ]
    if not ring:
        return None
    ring = [[float(x), float(y)] for x, y in ring]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def load_plots(ws):
    rows = ws.get_all_values()
    if not rows:
        return []
    header = rows[0]
    cols = {f: idx(header, names) for f, names in FIELD_COLUMNS.items()}
    if cols["plot_id"] is None:
        sys.exit("could not find plot id column in 'SunMint Plots' tab")
    # Loud-not-silent: a field with no matching header is otherwise dropped from the
    # registry without a trace -- warn so a renamed/typo'd column cannot vanish a tag.
    for _f, _i in cols.items():
        if _i is None:
            print(
                f"WARN: no column matches '{_f}' in '{SHEET_TAB}' tab; "
                "that field will be omitted from plots/index.geojson"
            )
    plots = []
    for row in rows[1:]:
        if not any((v or "").strip() for v in row):
            continue
        pid = cell(row, cols["plot_id"])
        if not pid:
            continue
        status = cell(row, cols["status"]) or "proposed"
        if str(status).strip().upper() == "INVALID":
            continue
        ring = parse_coordinates(
            cell(row, cols["coordinates"]),
            to_float(cell(row, cols["lat"])),
            to_float(cell(row, cols["lng"])),
            cell(row, cols["hectares"]),
        )
        if ring is None:
            # Keep the plot row but flag it: no geometry yet.
            ring = None
        media = cell(row, cols["media"])
        media_list = None
        if media:
            media_list = [m.strip() for m in media.split(";") if m.strip()] or None
        # Loud-not-silent media validation (2026-09-10): the impact map renders `media`
        # entries in an <img> from the sunmint repo (jsdelivr -> raw fallback). Anything
        # not browser-renderable silently shows a broken thumbnail -- and a bare
        # filename with no directory resolves to the repo ROOT, which 404s. Warn (never
        # silently accept) so this cannot recur for future plots.
        if media_list:
            for _m in media_list:
                _ml = _m.lower().split("?")[0].split("#")[0]
                if _ml.endswith((".heic", ".heif", ".mov", ".mp4")):
                    print(
                        f"WARN: plot {pid} media '{_m}' is not browser-renderable "
                        "(HEIC/MOV/MP4) -- convert to JPG/PNG and reference a "
                        "repo-relative path, or the impact-map thumbnail breaks"
                    )
                elif not _m.startswith("http") and "/" not in _m:
                    print(
                        f"WARN: plot {pid} media '{_m}' has no directory component "
                        "(bare filename) -- resolves against the repo root and 404s; "
                        "use a repo-relative path e.g. images/boundaries/<plot_id>/x.jpg"
                    )
        plot_type = cell(row, cols["plot_type"])
        if plot_type and plot_type.strip().lower() not in VALID_PLOT_TYPES:
            print(
                f"WARN: plot {pid} has unrecognized plot_type '{plot_type}' "
                f"(expected one of: {', '.join(sorted(VALID_PLOT_TYPES))})"
            )
        plots.append(
            {
                "plot_id": pid,
                "farm_id": cell(row, cols["farm_id"]) or None,
                "name": cell(row, cols["name"]) or pid,
                "hectares": to_float(cell(row, cols["hectares"])),
                "status": status,
                "plot_type": plot_type,
                "boundary_authority": cell(row, cols["boundary_authority"]) or "approx",
                "owner": cell(row, cols["owner"]) or None,
                "region": cell(row, cols["region"]) or None,
                "verified_at": cell(row, cols["verified_at"]) or None,
                "media": media_list,
                "notes": cell(row, cols["notes"]) or None,
                "_ring": ring,
            }
        )
    return plots


def plot_props(p):
    """Build the GeoJSON `properties` dict for one plot. None-valued keys are dropped,
    so an unclassified `plot_type` is simply absent (never a fabricated default)."""
    props = {
        "plot_id": p["plot_id"],
        "farm_id": p["farm_id"],
        "name": p["name"],
        "hectares": p["hectares"],
        "status": p["status"],
        "plot_type": p["plot_type"],
        "boundary_authority": p["boundary_authority"],
        "owner": p["owner"],
        "region": p["region"],
        "verified_at": p["verified_at"],
        "media": p["media"],
        "notes": p["notes"],
    }
    return {k: v for k, v in props.items() if v is not None}


def emit_per_plot(features, out_dir, generated_at):
    """Write one FeatureCollection per plot (derived layer) + prune stale files.

    The aggregate plots/index.geojson remains the serving artifact (single fetch
    for the app); per-plot files are a derived layer for retraction recalc,
    audit/lineage and fine-grained diffs. One source of truth (the sheet tab),
    two derived artifacts.
    """
    import os
    import re

    os.makedirs(out_dir, exist_ok=True)
    written = set()
    for f in features:
        pid = (f.get("properties") or {}).get("plot_id")
        if not pid:
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(pid))
        path = os.path.join(out_dir, safe + ".geojson")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "type": "FeatureCollection",
                    "generated_at": generated_at,
                    "features": [f],
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
        written.add(path)
        print(f"wrote {path}")
    # Prune stale per-plot files (removed/invalid plots) so the derived layer
    # never drifts from the aggregate.
    for name in os.listdir(out_dir):
        if not name.endswith(".geojson"):
            continue
        stale = os.path.join(out_dir, name)
        if stale not in written:
            os.remove(stale)
            print(f"pruned stale {stale}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plots/index.geojson")
    ap.add_argument("--by-plot-dir", default="plots/by-plot")
    args = ap.parse_args()

    try:
        ws = get_sheet()
        plots = load_plots(ws)
    except (Exception, SystemExit) as e:
        # Tab missing / auth / any failure -> preserve the existing registry.
        print(
            f"WARN: could not read '{SHEET_TAB}' tab ({e}); preserving existing registry"
        )
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
            print(
                f"preserved {len(existing.get('features', []))} features at {args.out}"
            )
            return
        sys.exit(f"no source tab and no existing {args.out} to preserve")

    if not plots:
        print("WARN: 'SunMint Plots' tab has no rows; preserving existing registry")
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
            print(
                f"preserved {len(existing.get('features', []))} features at {args.out}"
            )
            return
        sys.exit(f"no rows and no existing {args.out} to preserve")

    features = []
    for p in plots:
        props = plot_props(p)
        features.append(
            {
                "type": "Feature",
                "geometry": (
                    {"type": "Polygon", "coordinates": [p["_ring"]]}
                    if p["_ring"]
                    else None
                ),
                "properties": props,
            }
        )

    out = {
        "type": "FeatureCollection",
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "features": features,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(features)} plot features to {args.out}")
    emit_per_plot(features, args.by_plot_dir, out["generated_at"])


if __name__ == "__main__":
    main()
