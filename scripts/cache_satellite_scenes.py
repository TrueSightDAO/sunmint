#!/usr/bin/env python3
"""Cache Sentinel-2 preview scenes for SunMint trees into satellite/<lat>_<lng>/.

Treasury-cache pattern (same as build_tree_geojson.py): reads trees/index.geojson
(the tree index), queries the Copernicus Data Space Ecosystem (CDSE) STAC
catalogue for recent low-cloud Sentinel-2 scenes covering each 0.01-degree grid
cell that contains trees, and stores small preview JPEGs under
satellite/<lat>_<lng>/<scene-date>.jpg plus a satellite/manifest.json index.

Designed to DEGRADE GRACEFULLY: without CDSE credentials (CDSE_CLIENT_ID /
CDSE_CLIENT_SECRET) or while the Sentinel-2 collection is not yet exposed on
the STAC catalogue, it logs a warning and exits 0 so the workflow never fails
on missing configuration. The tree map keeps working from trees/index.geojson
regardless; the satellite layer is an enhancement, not a single point of failure.

Usage:
    python3 scripts/cache_satellite_scenes.py [--index trees/index.geojson] [--out-dir satellite]

Env:
    CDSE_CLIENT_ID, CDSE_CLIENT_SECRET  (optional until CDSE registration is done)
"""

import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
# Collection ids change as CDSE rolls out the new catalogue; try known candidates.
SENTINEL2_COLLECTIONS = [
    "sentinel-2-l2a",
    "sentinel-2",
    "SENTINEL-2",
    "sentinel2-l2a",
    "sentinel-2-l1c",
]
# Prefer small preview-style assets, never the full product archives.
PREFERRED_ASSET_KEYS = [
    "thumbnail",
    "preview",
    "rendered_preview",
    "visual",
    "overview",
    "truecolor",
    "info",
]
MAX_SCENES_PER_CELL = 4
DAYS_BACK = 30
GRID_DEG = 0.01  # ~1 km cells -> one folder per lat_lng


def log(msg):
    print(f"[satellite-cache] {msg}", flush=True)


def warn(msg):
    print(f"[satellite-cache] WARNING: {msg}", flush=True)


def get_token(client_id, client_secret):
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def stac_search(token, collection, bbox, datetime_range):
    body = json.dumps(
        {
            "collections": [collection],
            "bbox": bbox,
            "datetime": datetime_range,
            "limit": MAX_SCENES_PER_CELL,
            "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(STAC_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def pick_asset_url(item):
    assets = item.get("assets", {}) or {}
    for key in PREFERRED_ASSET_KEYS:
        if key in assets:
            href = assets[key].get("href")
            if href:
                return href
    # Fall back to the first non-data asset.
    for key, asset in assets.items():
        if key != "data" and asset.get("href"):
            return asset["href"]
    return None


def download(url, token, out_path):
    headers = {"User-Agent": "sunmint-satellite-cache"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 1000:
        raise ValueError(f"suspiciously small download ({len(data)} bytes)")
    with open(out_path, "wb") as f:
        f.write(data)


def grid_key(lon, lat):
    return f"{round(math.floor(lat / GRID_DEG) * GRID_DEG + GRID_DEG / 2, 4)}_{round(math.floor(lon / GRID_DEG) * GRID_DEG + GRID_DEG / 2, 4)}"


def load_index(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    feats = data.get("features", [])
    cells = {}
    for feat in feats:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue  # tree without coordinates
        lon, lat = coords[0], coords[1]
        key = grid_key(lon, lat)
        cells.setdefault(key, {"trees": 0, "bbox": [lon, lat, lon, lat]})
        cells[key]["trees"] += 1
        b = cells[key]["bbox"]
        b[0] = min(b[0], lon)
        b[1] = min(b[1], lat)
        b[2] = max(b[2], lon)
        b[3] = max(b[3], lat)
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="trees/index.geojson")
    ap.add_argument("--out-dir", default="satellite")
    args = ap.parse_args()

    client_id = os.environ.get("CDSE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CDSE_CLIENT_SECRET", "").strip()

    if not (client_id and client_secret):
        warn(
            "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not set - skipping satellite cache "
            "(register at https://dataspace.copernicus.eu and add repo secrets to enable)."
        )
        return 0

    token = None
    try:
        token = get_token(client_id, client_secret)
        log("CDSE token acquired")
    except Exception as e:
        warn(
            f"could not acquire CDSE token ({e}) - skipping. "
            "Check CDSE_CLIENT_ID/CDSE_CLIENT_SECRET are valid."
        )
        return 0

    if not os.path.exists(args.index):
        warn(f"tree index not found at {args.index} - skipping.")
        return 0
    cells = load_index(args.index)
    if not cells:
        warn("no tree coordinates found in index - nothing to cache.")
        return 0
    log(
        f"found {sum(c['trees'] for c in cells.values())} trees in {len(cells)} grid cells"
    )

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    start = (now - __import__("datetime").timedelta(days=DAYS_BACK)).isoformat()
    end = now.isoformat()
    dt_range = f"{start}/{end}"

    os.makedirs(args.out_dir, exist_ok=True)
    manifest = {"updated": now.isoformat(), "cells": {}}

    for cell, info in sorted(cells.items()):
        lat, lon = cell.split("_")
        # search with the tree bbox padded slightly
        pad = 0.001
        tree_bbox = [
            info["bbox"][0] - pad,
            info["bbox"][1] - pad,
            info["bbox"][2] + pad,
            info["bbox"][3] + pad,
        ]
        scenes = []
        for collection in SENTINEL2_COLLECTIONS:
            try:
                res = stac_search(token, collection, tree_bbox, dt_range)
                feats = res.get("features", [])
                if feats:
                    log(f"cell {cell}: {len(feats)} scene(s) from {collection}")
                    scenes = feats
                    break
            except Exception as e:
                warn(f"cell {cell}: search {collection} failed ({e})")
        if not scenes:
            warn(f"cell {cell}: no Sentinel-2 scenes found - skipping")
            continue
        cell_dir = os.path.join(args.out_dir, cell)
        os.makedirs(cell_dir, exist_ok=True)
        cell_manifest = []
        for item in scenes:
            scene_id = item.get("id", "scene")
            scene_date = (item.get("properties", {}).get("datetime") or "unknown")[:10]
            url = pick_asset_url(item)
            if not url:
                warn(f"cell {cell}: scene {scene_id} has no preview asset - skipped")
                continue
            fname = f"{scene_date}.jpg"
            out_path = os.path.join(cell_dir, fname)
            try:
                download(url, token, out_path)
                cell_manifest.append(
                    {"scene": scene_id, "date": scene_date, "file": f"{cell}/{fname}"}
                )
                log(f"cell {cell}: cached {fname} ({os.path.getsize(out_path)} bytes)")
            except Exception as e:
                warn(f"cell {cell}: download {scene_id} failed ({e}) - skipped")
        manifest["cells"][cell] = cell_manifest

    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    log(f"done - {len(manifest['cells'])} cells cached, manifest written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
