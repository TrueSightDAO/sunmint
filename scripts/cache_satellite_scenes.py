#!/usr/bin/env python3
"""Cache Sentinel-2 preview scenes for SunMint trees into satellite/<lat>_<lng>/.

Treasury-cache pattern (same as build_tree_geojson.py): reads trees/index.geojson
(the tree index) and plots/index.geojson (the plot registry), queries the Earth
Search STAC catalogue (AWS-hosted Sentinel-2 L2A by Element84 -- anonymous, no
API key) for low-cloud scenes, and stores small preview JPEGs under
satellite/<cell>/<date>.jpg and satellite/plot_<id>/<date>.jpg plus a
satellite/manifest.json index.

Self-driving catch-up daemon
----------------------------
This script reaches the full Sentinel-2 archive *by itself*, spread across the
daily cron runs (.github/workflows/cache-satellite-scenes.yml); no manual or
workflow_dispatch trigger is ever required:

* Steady state (recent window). Every run queries the last --recent-days
  (default 45) of low-cloud scenes for each grid cell and plot and *merges* them
  into the committed satellite/manifest.json (accumulate; never rebuild).
* Catch-up phase (per plot). Each run also walks exactly one bounded step
  *backward* in time (--backfill-step-days, default 365) for every plot that has
  not yet reached the Sentinel-2 archive floor (--archive-floor, default
  2015-06-23), pulling that window's low-cloud scenes and merging them. A plot is
  marked caught_up once its history_start reaches the floor.

Progress persists in satellite/manifest.json per plot via history_start +
caught_up, so successive daily runs advance the backfill one step at a time until
the whole archive is committed, then settle into rolling-window mode. Downloads
per run are capped (--max-downloads-per-run) to respect STAC/rate limits. The
script degrades gracefully: if STAC is unreachable or no scenes are found it logs
a warning and exits 0 so the workflow never fails.

Usage:
    python3 scripts/cache_satellite_scenes.py [--index trees/index.geojson] \
        [--plots plots/index.geojson] [--out-dir satellite]

No credentials are required -- Earth Search STAC is anonymous.
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
PREFERRED_ASSET_KEYS = [
    "thumbnail",
    "preview",
    "rendered_preview",
    "visual",
    "overview",
    "info",
]
MAX_SCENES_PER_CELL = 4  # steady-state scenes per grid cell
MAX_SCENES_PER_QUERY = 200  # hard cap on a single STAC page walk
DEFAULT_RECENT_DAYS = 45
DEFAULT_BACKFILL_STEP_DAYS = 365
DEFAULT_ARCHIVE_FLOOR = "2015-06-23"  # Sentinel-2A first light
DEFAULT_MAX_DOWNLOADS_PER_RUN = 450
CLOUD_MAX = 20.0  # only commit low-cloud scenes (durable-evidence subset)
GRID_DEG = 0.01  # ~1 km cells -> one folder per lat_lng
TIMEOUT_SECS = 60
DT_FMT = "%Y-%m-%dT%H:%M:%SZ"


def log(msg):
    print(f"[satellite-cache] {msg}", flush=True)


def warn(msg):
    print(f"[satellite-cache] WARNING: {msg}", flush=True)


def cell_key(lat, lng):
    return f"{round(math.floor(lat / GRID_DEG) * GRID_DEG, 2)}_{round(math.floor(lng / GRID_DEG) * GRID_DEG, 2)}"


def _get_asset_url(feature):
    """Return the best small preview asset URL for a STAC feature (prefer tiny)."""
    assets = feature.get("assets", {})
    for key in PREFERRED_ASSET_KEYS:
        if key in assets:
            return assets[key].get("href")
    # Fall back to the first asset whose type is a JPEG.
    for a in assets.values():
        if a.get("type", "").startswith("image/jpeg"):
            return a.get("href")
    return None


def _stac_request(url, body=None):
    """Issue a STAC request: POST when ``body`` is a dict, else GET.

    Returns the parsed JSON, or None on any failure (graceful by design).
    """
    if body is not None:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "sunmint-cache/1.0",
            },
            method="POST",
        )
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "sunmint-cache/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - graceful degradation is by design
        warn(f"STAC request failed for {url}: {exc}")
        return None


def _cloud(feature):
    c = feature.get("properties", {}).get("eo:cloud_cover")
    return c if c is not None else 999.0


def query_stac(bbox, start_dt, end_dt, cloud_max, max_features=MAX_SCENES_PER_QUERY):
    """Query Earth Search STAC for low-cloud Sentinel-2 scenes over a bbox within
    [start_dt, end_dt]. Paginates via 'next' links (bounded to 10 pages). Returns
    features sorted by cloud cover ascending, capped at ``max_features``.
    """
    payload = {
        "collections": [COLLECTION],
        "bbox": bbox,
        "datetime": f"{start_dt.strftime(DT_FMT)}/{end_dt.strftime(DT_FMT)}",
        "limit": 100,
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        "query": {"eo:cloud_cover": {"lt": cloud_max}},
    }
    features = []
    url, body = STAC_URL, payload
    for _ in range(10):
        data = _stac_request(url, body)
        if data is None:
            break
        features.extend(data.get("features", []))
        if len(features) >= max_features:
            break
        nxt = next(
            (lnk for lnk in data.get("links", []) if lnk.get("rel") == "next"), None
        )
        if not nxt:
            break
        if nxt.get("body"):
            url, body = STAC_URL, dict(nxt["body"])
        elif nxt.get("href"):
            url, body = nxt["href"], None
        else:
            break
    features.sort(key=_cloud)
    return features[:max_features]


def download(url, dest):
    """Download url to dest; returns bytes written or None on failure (kept out of
    manifest if it fails, so the manifest never references a missing file)."""
    req = urllib.request.Request(url, headers={"User-Agent": "sunmint-cache/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            blob = resp.read()
            if resp.status != 200 or not blob:
                warn(f"download {url} -> HTTP {resp.status}")
                return None
            with open(dest, "wb") as fh:
                fh.write(blob)
            return len(blob)
    except Exception as exc:  # noqa: BLE001
        warn(f"download {url} failed: {exc}")
        return None


def _load_manifest(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            warn(f"existing manifest unreadable ({exc}); starting fresh")
    return {}


def merge_scenes(existing, new_scenes, per_query_cap):
    """Merge ``new_scenes`` into ``existing`` (dict keyed by date), newest-first,
    dedupe by date, keep at most ``per_query_cap`` scenes."""
    by_date = {s.get("date"): s for s in existing if s.get("date")}
    for s in new_scenes:
        if s.get("date") and s["date"] not in by_date:
            by_date[s["date"]] = s
    return sorted(by_date.values(), key=lambda s: s.get("date", ""), reverse=True)[
        :per_query_cap
    ]


def _fetch_scenes(bbox, start_dt, end_dt, cloud_max, cell_dir, cap, budget):
    """Query STAC then download each scene's preview into ``cell_dir``.

    Returns (scene_dicts, downloads_used). Stops early when ``budget`` downloads
    are exhausted. Scenes whose preview fails to download are dropped so the
    manifest never points at a missing file.
    """
    out, used = [], 0
    for i, feat in enumerate(
        query_stac(bbox, start_dt, end_dt, cloud_max, max_features=cap)
    ):
        if used >= budget:
            break
        props = feat.get("properties", {})
        scene_id = feat.get("id", f"scene_{i}")
        date_str = (props.get("datetime") or "")[:10]
        asset_url = _get_asset_url(feat)
        if not asset_url or not date_str:
            continue
        fname = f"{date_str.replace('-', '')}.jpg"
        dest = os.path.join(cell_dir, fname)
        if not os.path.exists(dest):
            size = download(asset_url, dest)
            used += 1
            if size is None:
                continue
        else:
            size = os.path.getsize(dest)
        out.append(
            {
                "id": scene_id,
                "date": date_str,
                "cloud_cover": round(props.get("eo:cloud_cover") or 0.0, 3),
                "file": fname,
                "bytes": size,
                "asset_url": asset_url,
            }
        )
    return out, used


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main():
    parser = argparse.ArgumentParser(
        description="Cache Sentinel-2 previews for SunMint trees"
    )
    parser.add_argument("--index", default="trees/index.geojson")
    parser.add_argument("--plots", default="plots/index.geojson")
    parser.add_argument("--out-dir", default="satellite")
    parser.add_argument("--recent-days", type=int, default=DEFAULT_RECENT_DAYS)
    parser.add_argument(
        "--backfill-step-days", type=int, default=DEFAULT_BACKFILL_STEP_DAYS
    )
    parser.add_argument("--archive-floor", default=DEFAULT_ARCHIVE_FLOOR)
    parser.add_argument("--cloud-max", type=float, default=CLOUD_MAX)
    parser.add_argument(
        "--max-downloads-per-run", type=int, default=DEFAULT_MAX_DOWNLOADS_PER_RUN
    )
    args = parser.parse_args()

    # Hard guard: trees/plots.geojson was a dead duplicate that silently skipped
    # plot-level caching. Never accept it (see repo README).
    if args.plots.replace("\\", "/").endswith("trees/plots.geojson"):
        sys.exit(
            "REFUSING trees/plots.geojson: the only plot registry is "
            "plots/index.geojson (see README)"
        )

    if not os.path.exists(args.index):
        warn(f"index not found: {args.index} -- nothing to do (exit 0)")
        return 0

    with open(args.index, encoding="utf-8") as fh:
        index = json.load(fh)

    plots = []
    if os.path.exists(args.plots):
        with open(args.plots, encoding="utf-8") as fh:
            plots_fc = json.load(fh)
        for feat in plots_fc.get("features", []):
            props = feat.get("properties", {})
            geom = feat.get("geometry") or {}
            if geom.get("type") != "Polygon":
                continue
            ring = geom.get("coordinates", [[]])[0]
            lngs = [c[0] for c in ring if len(c) >= 2]
            lats = [c[1] for c in ring if len(c) >= 2]
            if not lngs or not lats:
                continue
            plots.append(
                {
                    "id": props.get("plot_id")
                    or props.get("id")
                    or f"plot_{len(plots)}",
                    "name": props.get("name") or props.get("plot_id") or "Plot",
                    "bbox": [min(lngs), min(lats), max(lngs), max(lats)],
                    "center": {
                        "lat": (min(lats) + max(lats)) / 2.0,
                        "lng": (min(lngs) + max(lngs)) / 2.0,
                    },
                }
            )

    cells = {}
    for feat in index.get("features", []):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        if props.get("is_test"):
            continue
        coords = (
            geom.get("coordinates") if geom and geom.get("type") == "Point" else None
        )
        if not coords or len(coords) < 2:
            continue
        lng, lat = coords[0], coords[1]
        key = cell_key(lat, lng)
        cells.setdefault(key, {"lat": lat, "lng": lng, "trees": 0})
        cells[key]["trees"] += 1

    if not cells and not plots:
        warn("no tree coordinates and no plots -- nothing to do (exit 0)")
        return 0

    log(
        f"found {len(cells)} grid cells from {len(index.get('features', []))} index "
        f"features and {len(plots)} plots"
    )

    os.makedirs(args.out_dir, exist_ok=True)
    manifest_path = os.path.join(args.out_dir, "manifest.json")
    prev = _load_manifest(manifest_path)
    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(days=args.recent_days)
    floor = _parse_date(args.archive_floor)

    manifest = {
        "generated_at": now.isoformat(),
        "source": "earth-search.aws.element84.com Sentinel-2 L2A (anonymous)",
        "cloud_max": args.cloud_max,
        "archive_floor": args.archive_floor,
        "cells": dict(prev.get("cells", {})),
        "plots": dict(prev.get("plots", {})),
    }

    budget = args.max_downloads_per_run

    # ---- plots: steady-state recent window + one bounded catch-up step backward
    for plot in plots:
        bbox = plot["bbox"]
        plot_dir = os.path.join(args.out_dir, "plot_" + plot["id"])
        os.makedirs(plot_dir, exist_ok=True)
        plot_meta = manifest["plots"].get(plot["id"], {})
        plot_meta.update(
            {
                "id": plot["id"],
                "name": plot["name"],
                "bbox": bbox,
                "center": plot["center"],
            }
        )
        existing = plot_meta.get("scenes", [])

        # (a) rolling recent window (always)
        if budget > 0:
            new_scenes, used = _fetch_scenes(
                bbox,
                recent_start,
                now,
                args.cloud_max,
                plot_dir,
                MAX_SCENES_PER_QUERY,
                budget,
            )
            budget -= used
            existing = merge_scenes(existing, new_scenes, MAX_SCENES_PER_QUERY)

        # (b) one catch-up step backward, if history has not reached the floor
        # (b) one bounded catch-up step backward. The cursor is an explicit, persisted
        # date (backfill_cursor), NOT derived from the earliest cached scene: dry-season
        # windows often yield ZERO low-cloud scenes, and a scene-derived cursor would
        # then stall forever on the same window and never reach the floor.
        cursor = plot_meta.get("backfill_cursor")
        if cursor:
            cur_dt = _parse_date(cursor)
        else:
            earliest = min(
                (s.get("date") for s in existing if s.get("date")), default=None
            )
            cur_dt = _parse_date(earliest) if earliest else now
        caught_up = cur_dt <= floor
        if not caught_up and budget > 0:
            step_end = cur_dt - timedelta(days=1)
            step_start = max(step_end - timedelta(days=args.backfill_step_days), floor)
            if step_start < step_end:
                log(
                    f"plot {plot['id']}: catch-up {step_start.date()}..{step_end.date()} "
                    f"(floor {floor.date()})"
                )
                new_scenes, used = _fetch_scenes(
                    bbox,
                    step_start,
                    step_end,
                    args.cloud_max,
                    plot_dir,
                    MAX_SCENES_PER_QUERY,
                    budget,
                )
                budget -= used
                existing = merge_scenes(existing, new_scenes, MAX_SCENES_PER_QUERY)
                cur_dt = step_start
                caught_up = cur_dt <= floor

        plot_meta["scenes"] = existing
        plot_meta["backfill_cursor"] = cur_dt.strftime("%Y-%m-%d")
        plot_meta["history_start"] = min(
            (s.get("date") for s in existing if s.get("date")),
            default=args.archive_floor,
        )
        plot_meta["caught_up"] = caught_up
        if existing:
            manifest["plots"][plot["id"]] = plot_meta
            log(
                f"plot {plot['id']}: {len(existing)} scenes cached "
                f"(history_start={plot_meta['history_start']}, caught_up={caught_up})"
            )

    # ---- grid cells: steady-state recent window only (tree-level foliage point)
    for key in sorted(cells):
        info = cells[key]
        if budget <= 0:
            break
        pad = GRID_DEG / 2.0
        bbox = [
            info["lng"] - pad,
            info["lat"] - pad,
            info["lng"] + pad,
            info["lat"] + pad,
        ]
        cell_dir = os.path.join(args.out_dir, key)
        os.makedirs(cell_dir, exist_ok=True)
        cell_meta = manifest["cells"].get(key, {})
        cell_meta.update(
            {
                "center": {"lat": round(info["lat"], 5), "lng": round(info["lng"], 5)},
                "trees": info["trees"],
            }
        )
        new_scenes, used = _fetch_scenes(
            bbox,
            recent_start,
            now,
            args.cloud_max,
            cell_dir,
            MAX_SCENES_PER_CELL,
            budget,
        )
        budget -= used
        cell_meta["scenes"] = merge_scenes(
            cell_meta.get("scenes", []), new_scenes, MAX_SCENES_PER_CELL
        )
        if cell_meta["scenes"]:
            manifest["cells"][key] = cell_meta
            log(f"cell {key}: {len(cell_meta['scenes'])} scenes cached")

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    total_cells = sum(len(m["scenes"]) for m in manifest["cells"].values())
    total_plots = sum(len(m["scenes"]) for m in manifest["plots"].values())
    pending = [p["id"] for p in manifest["plots"].values() if not p.get("caught_up")]
    log(
        f"done: {len(manifest['cells'])} cells ({total_cells} scenes), "
        f"{len(manifest['plots'])} plots ({total_plots} scenes) -> {manifest_path}; "
        f"{len(pending)} plots still backfilling"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
