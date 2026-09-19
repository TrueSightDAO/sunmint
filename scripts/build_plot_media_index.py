"""Build the SunMint per-plot media index (plots/media.json).

Joins the farm media manifests (TrueSightDAO/farm_media_manifests) to the plot
registry (plots/index.geojson) so the Plot Explorer can render, per plot, the
media that belongs to it -- without an N-fetch fan-out across farm galleries.

Attribution order (first match wins, most authoritative first):
  1. explicit item ``plot_id`` (authoritative; e.g. cacau-na-veia, sitio-torres)
  2. point-in-polygon -- item ``lat``/``lng`` inside a plot polygon
  3. manifest-level single ``plot_ids``/``plots`` entry (farm has exactly one plot)

Anything else is UNATTRIBUTED: recorded under ``unattributed[farm_id]`` and
WARNed loudly -- never silently dropped (loud-not-silent convention).

Freshness (plan section 6a): the output carries an index-level ``generated_at``,
per-plot ``updated``, and the source ``plots_index_sha256`` + per-manifest
``updated``. Writes are IDEMPOTENT: ``generated_at`` is only bumped when the
content actually changes, so a no-op reconcile produces no diff (no commit).

Usage:
  python3 scripts/build_plot_media_index.py \
      --manifests-dir /path/to/farm_media_manifests \
      --plots plots/index.geojson --out plots/media.json
  python3 scripts/build_plot_media_index.py --check ...    # drift gate (exit 1)
  python3 scripts/build_plot_media_index.py --stdout ...   # print, do not write
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

SCHEMA_VERSION = 1
CADENCE_MINUTES = 15

YOUTUBE_WATCH = "https://www.youtube.com/watch?v="
YOUTUBE_THUMB = "https://img.youtube.com/vi/{}/hqdefault.jpg"

# Keys a manifest may hold its item list under (checked in order).
_ITEM_KEYS = ("items", "videos", "media")
# Sub-directories that hold published derivatives, not raw manifests.
_SKIP_DIRS = ("galleries", "transcripts")


def _warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def _first(d: dict, keys) -> object:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _norm_date(v):
    """Normalise a date-ish stamp to YYYY-MM-DD; tolerate ':' separators (a real
    manifest bug seen live: '2026:09:09') and trailing times."""
    if not isinstance(v, str):
        return v
    head = v.strip()[:10].replace(":", "-").replace("/", "-")
    parts = head.split("-")
    if len(parts) == 3 and all(x.isdigit() for x in parts):
        return head
    return v


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_staleness(
    index: dict,
    cadence_minutes: int = CADENCE_MINUTES,
    *,
    now: datetime.datetime | None = None,
    multiplier: int = 2,
) -> str | None:
    """Return an alert string if the index is overdue, else None.

    The index is overdue when its ``generated_at`` age exceeds
    ``multiplier x cadence_minutes`` (the plan's section 6a monitor). A missing
    or unparsable stamp is itself an alert -- silent staleness is the enemy.
    """
    stamp = (index or {}).get("generated_at")
    if not stamp:
        return "index has no generated_at stamp"
    try:
        gen = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return f"index generated_at is unparsable: {stamp!r}"
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    age_min = (now - gen).total_seconds() / 60.0
    limit = multiplier * cadence_minutes
    if age_min > limit:
        return (
            f"index is STALE: generated_at {stamp} is {age_min:.0f} min old "
            f"(limit {limit} min = {multiplier}x cadence)"
        )
    return None


def _norm_scalar(v):
    """Return a JSON-scalar or None (strip empty strings/lists)."""
    if v in (None, "", [], {}):
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


# --------------------------------------------------------------------------- #
# plot registry
# --------------------------------------------------------------------------- #
def load_plots(path: str) -> tuple[dict, str]:
    """Load plots/index.geojson -> ({plot_id: plot}, sha256-of-raw-bytes)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    sha = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw.decode("utf-8"))
    plots: dict[str, dict] = {}
    for feat in doc.get("features", []):
        props = feat.get("properties", {}) or {}
        pid = props.get("plot_id")
        if not pid:
            continue
        plots[str(pid)] = {
            "plot_id": str(pid),
            "farm_id": props.get("farm_id"),
            "name": props.get("name"),
            "geometry": feat.get("geometry") or {},
        }
    return plots, sha


def _rings(geometry: dict) -> list:
    """Flatten a Polygon/MultiPolygon geometry into a list of rings (each a list of coord pairs)."""
    gtype = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates") or []
    if gtype == "Polygon":
        return [coords[0]] if coords else []
    if gtype == "MultiPolygon":
        return [poly[0] for poly in coords if poly]
    return []


def point_in_ring(lng: float, lat: float, ring: list) -> bool:
    """Ray-casting point-in-polygon; ring is a closed [[lng, lat], ...]."""
    if len(ring) < 3:
        return False
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def plot_containing(plots: dict, lng, lat):
    """Return the plot_id whose polygon contains (lng, lat), else None."""
    if lng is None or lat is None:
        return None
    try:
        lng = float(lng)
        lat = float(lat)
    except (TypeError, ValueError):
        return None
    for pid, plot in plots.items():
        for ring in _rings(plot["geometry"]):
            if point_in_ring(lng, lat, ring):
                return pid
    return None


# --------------------------------------------------------------------------- #
# manifests
# --------------------------------------------------------------------------- #
def get_items(doc) -> list:
    """Return the manifest's item list across the known shapes."""
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if not isinstance(doc, dict):
        return []
    for k in _ITEM_KEYS:
        v = doc.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def manifest_farm_id(doc: dict, filename: str) -> str:
    return str(
        _first(doc, ("farm_id", "site_id", "farm", "site"))
        or os.path.splitext(os.path.basename(filename))[0]
    )


def manifest_updated(doc: dict, path: str) -> str:
    """Best-effort 'updated' stamp for a manifest (first present wins)."""
    v = _first(
        doc,
        ("updated", "generated", "generated_at_utc", "generated_at", "captured"),
    )
    if isinstance(v, str) and len(v) >= 10:
        return _norm_date(v)
    ts = os.path.getmtime(path)
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d"
    )


def iter_manifest_files(root: str) -> list:
    """Top-level + farms/*.json that look like manifests; skip index + derivatives."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        parts = set(os.path.relpath(dirpath, root).split(os.sep))
        if parts & set(_SKIP_DIRS):
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".json") or fn == "index.json":
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p) as fh:
                    doc = json.load(fh)
            except (OSError, ValueError) as exc:  # pragma: no cover - defensive
                _warn(f"{p}: unreadable JSON ({exc}) -- skipped")
                continue
            if isinstance(doc, dict) and (
                get_items(doc) or doc.get("farm_id") or doc.get("site_id")
            ):
                out.append(p)
    return sorted(out)


def item_kind(item: dict) -> str:
    t = str(item.get("type") or "").lower()
    if "video" in t or item.get("yt_id") or item.get("duration_s") is not None:
        return "video"
    if "image" in t or "photo" in t:
        return "image"
    ext = str(item.get("ext") or item.get("file") or "").lower()
    return "video" if ext.endswith((".mov", ".mp4")) else "image"


def normalize_item(item: dict, attribution: str) -> dict:
    yt_id = item.get("yt_id")
    kind = item_kind(item)
    url = _first(item, ("url", "s3_url", "preview_url", "raw_url", "video_url"))
    if not url and yt_id:
        url = f"{YOUTUBE_WATCH}{yt_id}"
    ref = (
        item.get("file")
        or item.get("basename")
        or (str(item.get("sha256"))[:12] if item.get("sha256") else None)
        or url
    )
    return {
        "ref": str(ref) if ref else None,
        "kind": kind,
        "url": url,
        "thumbnail": YOUTUBE_THUMB.format(yt_id) if yt_id else None,
        "captured_at": _norm_scalar(
            _first(item, ("captured_at", "creation_date", "uploaded_at"))
        ),
        "lat": _norm_scalar(item.get("latitude")),
        "lng": _norm_scalar(item.get("longitude")),
        "sha256": _norm_scalar(item.get("sha256")),
        "transcript_status": _norm_scalar(item.get("transcription_status")),
        "attribution": attribution,
    }


def build_payload(manifests_dir: str, plots_path: str) -> dict:
    plots, plots_sha = load_plots(plots_path)
    per_plot: dict[str, dict] = {
        pid: {
            "plot_id": pid,
            "farm_id": p.get("farm_id"),
            "name": p.get("name"),
            "updated": None,
            "media": [],
        }
        for pid, p in plots.items()
    }
    unattributed: dict[str, list] = {}
    manifest_index: dict[str, str] = {}
    total = 0

    for path in iter_manifest_files(manifests_dir):
        with open(path) as fh:
            doc = json.load(fh)
        items = get_items(doc)
        if not items:
            continue
        farm = manifest_farm_id(doc, path)
        m_updated = manifest_updated(doc, path)
        manifest_index[farm] = max(manifest_index.get(farm, ""), m_updated)

        # manifest-level declared plots (fallback, only when unambiguous)
        mplots = []
        for key in ("plot_ids", "plots"):
            v = doc.get(key)
            if isinstance(v, list):
                mplots += [x for x in v if isinstance(x, str)]
        known = [p for p in dict.fromkeys(mplots) if p in plots]
        m_fallback = known[0] if len(known) == 1 else None

        for item in items:
            total += 1
            pid = item.get("plot_id")
            attribution = None
            if pid and str(pid) in plots:
                pid, attribution = str(pid), "plot_id"
            else:
                pid = plot_containing(
                    plots, item.get("longitude"), item.get("latitude")
                )
                attribution = "point-in-polygon" if pid else None
                if not pid and m_fallback:
                    pid, attribution = m_fallback, "manifest-plot-id"
            entry = normalize_item(item, attribution or "unattributed")
            if pid:
                bucket = per_plot[pid]
                bucket["media"].append(entry)
                stamp = (
                    _norm_date(entry.get("captured_at"))
                    if entry.get("captured_at")
                    else m_updated
                )
                if stamp:
                    cur = bucket["updated"] or ""
                    if str(stamp)[:10] > cur:
                        bucket["updated"] = str(stamp)[:10]
            else:
                unattributed.setdefault(farm, []).append(entry)

    unattributed_total = sum(len(v) for v in unattributed.values())
    if unattributed_total:
        _warn(
            f"{unattributed_total} media item(s) could not be attributed to a plot "
            f"(across {len(unattributed)} farm(s)); recorded under 'unattributed'. "
            "Add a plot geometry or an item plot_id to attribute them."
        )
        for farm, items in sorted(unattributed.items()):
            _warn(f"  unattributed: {farm} -> {len(items)} item(s)")

    for bucket in per_plot.values():
        bucket["media"].sort(
            key=lambda m: (str(m.get("captured_at") or ""), str(m.get("ref") or ""))
        )
        bucket["counts"] = {
            "image": sum(1 for m in bucket["media"] if m["kind"] == "image"),
            "video": sum(1 for m in bucket["media"] if m["kind"] == "video"),
            "total": len(bucket["media"]),
        }

    plots_out = {pid: per_plot[pid] for pid in sorted(per_plot)}
    return {
        "schema_version": SCHEMA_VERSION,
        "cadence_minutes": CADENCE_MINUTES,
        "source": {
            "manifests_repo": "TrueSightDAO/farm_media_manifests",
            "plots_index_sha256": plots_sha,
            "manifests": {k: manifest_index[k] for k in sorted(manifest_index)},
        },
        "counts": {
            "plots": len(plots_out),
            "plots_with_media": sum(1 for b in plots_out.values() if b["media"]),
            "media_total": total,
            "unattributed": unattributed_total,
        },
        "plots": plots_out,
        "unattributed": {k: unattributed[k] for k in sorted(unattributed)},
    }


def _core(obj: dict) -> str:
    """Serialise payload without volatile keys, for change detection."""
    clone = json.loads(json.dumps(obj))
    clone.pop("generated_at", None)
    return json.dumps(clone, sort_keys=True, separators=(",", ":"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifests-dir", required=True)
    ap.add_argument("--plots", default="plots/index.geojson")
    ap.add_argument("--out", default="plots/media.json")
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if on-disk index has drifted"
    )
    ap.add_argument("--stdout", action="store_true", help="print, do not write")
    ap.add_argument(
        "--check-staleness",
        action="store_true",
        help="exit 1 if the on-disk index is overdue",
    )
    args = ap.parse_args(argv)

    if not os.path.isdir(args.manifests_dir):
        _warn(f"manifests dir not found: {args.manifests_dir}")
        return 2
    if not os.path.exists(args.plots):
        _warn(f"plots registry not found: {args.plots}")
        return 2

    payload = build_payload(args.manifests_dir, args.plots)

    if args.check_staleness:
        if not os.path.exists(args.out):
            print(
                f"ERROR: {args.out} missing -- cannot check staleness", file=sys.stderr
            )
            return 1
        with open(args.out) as fh:
            disk_index = json.load(fh)
        alert = check_staleness(disk_index, payload["cadence_minutes"])
        if alert:
            print(f"ERROR: {alert}", file=sys.stderr)
            return 1
        print(f"OK: {args.out} is fresh")
        return 0

    if args.check:
        if not os.path.exists(args.out):
            print(f"ERROR: {args.out} missing -- regenerate", file=sys.stderr)
            return 1
        with open(args.out) as fh:
            existing = json.load(fh)
        if _core(existing) != _core(payload):
            print(
                f"ERROR: {args.out} has drifted from its inputs "
                "(run scripts/build_plot_media_index.py)",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {args.out} is in sync with its inputs")
        return 0

    if args.stdout:
        payload["generated_at"] = _iso_now()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    existing = None
    if os.path.exists(args.out):
        with open(args.out) as fh:
            existing = json.load(fh)
    if existing is not None and _core(existing) == _core(payload):
        print(f"unchanged: {args.out} ({payload['counts']['media_total']} media items)")
        return 0

    payload["generated_at"] = _iso_now()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    c = payload["counts"]
    print(
        f"wrote {args.out}: {c['plots_with_media']}/{c['plots']} plots with media, "
        f"{c['media_total']} items, {c['unattributed']} unattributed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
