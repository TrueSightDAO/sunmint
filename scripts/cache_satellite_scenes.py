#!/usr/bin/env python3
"""Cache Sentinel-2 preview scenes for SunMint trees into satellite/<lat>_<lng>/.

Treasury-cache pattern (same as build_tree_geojson.py): reads trees/index.geojson
(the tree index), queries the Earth Search STAC catalogue (AWS-hosted Sentinel-2
L2A by Element84 — no registration or API key required) for recent low-cloud
Sentinel-2 scenes covering each 0.01-degree grid cell that contains trees, and
stores small preview JPEGs under satellite/<lat>_<lng>/<scene-date>.jpg plus a
satellite/manifest.json index.

Degrades gracefully: if the STAC service is unreachable or no scenes are found,
logs a warning and exits 0 so the workflow never fails on missing configuration.
The tree map keeps working from trees/index.geojson regardless; the satellite
layer is an enhancement, not a single point of failure.

Usage:
    python3 scripts/cache_satellite_scenes.py [--index trees/index.geojson] [--out-dir satellite]

No credentials are required — Earth Search STAC is anonymous.
"""

import argparse
import json
import math
import os
import sys
import urllib.parse
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
MAX_SCENES_PER_CELL = 4
DAYS_BACK = 45
GRID_DEG = 0.01  # ~1 km cells -> one folder per lat_lng
TIMEOUT_SECS = 60


def log(msg):
    print(f"[satellite-cache] {msg}", flush=True)


def warn(msg):
    print(f"[satellite-cache] WARNING: {msg}", flush=True)


def _get_asset_url(feature):
    """Return the best small preview asset URL for a STAC feature (prefer tiny)."""
    assets = feature.get("assets", {})
    for key in PREFERRED_ASSET_KEYS:
        if key in assets:
            return assets[key].get("href")
    # Fall back to the first asset whose type is a JPEG.
    for _, a in assets.items():
        if a.get("type", "").startswith("image/jpeg"):
            return a.get("href")
    return None


def query_stac(cell_bbox):
    """Query Earth Search STAC for low-cloud Sentinel-2 scenes over a bbox.

    Returns features sorted by cloud cover ascending.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    payload = {
        "collections": [COLLECTION],
        "bbox": cell_bbox,
        "datetime": f"{since}/{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "limit": 20,
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }
    req = urllib.request.Request(
        STAC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "sunmint-cache/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - graceful degradation is by design
        warn(f"STAC query failed for bbox {cell_bbox}: {exc}")
        return []
    features = data.get("features", [])
    features.sort(
        key=lambda f: (
            f.get("properties", {}).get("eo:cloud_cover")
            if f.get("properties", {}).get("eo:cloud_cover") is not None
            else 999
        )
    )
    return features[:MAX_SCENES_PER_CELL]


def download(url, dest):
    """Download url to dest; returns bytes written or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "sunmint-cache/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            body = resp.read()
            if resp.status != 200 or not body:
                warn(f"download {url} -> HTTP {resp.status}")
                return None
            with open(dest, "wb") as fh:
                fh.write(body)
            return len(body)
    except Exception as exc:  # noqa: BLE001
        warn(f"download {url} failed: {exc}")
        return None


def cell_key(lat, lng):
    return f"{round(math.floor(lat / GRID_DEG) * GRID_DEG, 2)}_{round(math.floor(lng / GRID_DEG) * GRID_DEG, 2)}"


def main():
    parser = argparse.ArgumentParser(
        description="Cache Sentinel-2 previews for SunMint trees"
    )
    parser.add_argument("--index", default="trees/index.geojson")
    parser.add_argument("--plots", default="trees/plots.geojson")
    parser.add_argument("--out-dir", default="satellite")
    args = parser.parse_args()

    if not os.path.exists(args.index):
        warn(f"index not found: {args.index} — nothing to do (exit 0)")
        return 0

    with open(args.index, encoding="utf-8") as fh:
        index = json.load(fh)

    plots = []
    if os.path.exists(args.plots):
        with open(args.plots, encoding="utf-8") as fh:
            plots_fc = json.load(fh)
        for feat in plots_fc.get("features", []):
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
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

    if not cells:
        warn("no tree coordinates in index — nothing to do (exit 0)")
        return 0

    log(
        f"found {len(cells)} grid cells from {len(index.get('features', []))} index features"
    )

    os.makedirs(args.out_dir, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "earth-search.aws.element84.com Sentinel-2 L2A (anonymous)",
        "cells": {},
        "plots": {},
    }

    for key in sorted(cells):
        info = cells[key]
        pad = GRID_DEG / 2.0
        bbox = [
            info["lng"] - pad,
            info["lat"] - pad,
            info["lng"] + pad,
            info["lat"] + pad,
        ]
        scenes = query_stac(bbox)
        cell_dir = os.path.join(args.out_dir, key)
        os.makedirs(cell_dir, exist_ok=True)
        cell_meta = {
            "center": {"lat": round(info["lat"], 5), "lng": round(info["lng"], 5)},
            "trees": info["trees"],
            "scenes": [],
        }
        for i, feat in enumerate(scenes):
            props = feat.get("properties", {})
            scene_id = feat.get("id", f"scene_{i}")
            date_str = (props.get("datetime") or "")[:10]
            fname_date = date_str.replace("-", "") or scene_id.split("_")[-1]
            asset_url = _get_asset_url(feat)
            if not asset_url:
                continue
            # avoid collisions when multiple granules share a date
            fname = f"{fname_date}.jpg"
            dest = os.path.join(cell_dir, fname)
            n = 1
            while os.path.exists(dest) and i > 0:
                fname = f"{fname_date}_{n}.jpg"
                dest = os.path.join(cell_dir, fname)
                n += 1
            size = download(asset_url, dest)
            if size is None:
                continue
            cell_meta["scenes"].append(
                {
                    "id": scene_id,
                    "date": date_str,
                    "cloud_cover": round(props.get("eo:cloud_cover") or 0.0, 3),
                    "file": fname,
                    "bytes": size,
                    "asset_url": asset_url,
                }
            )
        if cell_meta["scenes"]:
            manifest["cells"][key] = cell_meta
            log(f"cell {key}: {len(cell_meta['scenes'])} scenes cached")

    # Plot-level caching: when plot boundaries exist, also cache one coherent
    # scene set per plot (clipped to its bbox). Keeps the grid-cell behavior
    # for trees without a plot.
    for plot in plots:
        bbox = plot["bbox"]
        scenes = query_stac(bbox)
        plot_dir = os.path.join(args.out_dir, "plot_" + plot["id"])
        os.makedirs(plot_dir, exist_ok=True)
        plot_meta = {
            "id": plot["id"],
            "name": plot["name"],
            "bbox": bbox,
            "center": plot["center"],
            "scenes": [],
        }
        for i, feat in enumerate(scenes):
            props = feat.get("properties", {})
            scene_id = feat.get("id", f"scene_{i}")
            date_str = (props.get("datetime") or "")[:10]
            fname_date = date_str.replace("-", "") or scene_id.split("_")[-1]
            asset_url = _get_asset_url(feat)
            if not asset_url:
                continue
            fname = f"{fname_date}.jpg"
            dest = os.path.join(plot_dir, fname)
            n = 1
            while os.path.exists(dest) and i > 0:
                fname = f"{fname_date}_{n}.jpg"
                dest = os.path.join(plot_dir, fname)
                n += 1
            size = download(asset_url, dest)
            if size is None:
                continue
            plot_meta["scenes"].append(
                {
                    "id": scene_id,
                    "date": date_str,
                    "cloud_cover": round(props.get("eo:cloud_cover") or 0.0, 3),
                    "file": fname,
                    "bytes": size,
                    "asset_url": asset_url,
                }
            )
        if plot_meta["scenes"]:
            manifest["plots"][plot["id"]] = plot_meta
            log(f"plot {plot['id']}: {len(plot_meta['scenes'])} scenes cached")

    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    total = sum(len(m["scenes"]) for m in manifest["cells"].values())
    log(
        f"done: {len(manifest['cells'])} cells, {total} scenes cached -> {args.out_dir}/manifest.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
