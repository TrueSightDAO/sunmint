#!/usr/bin/env python3
"""Build sunmint/trees/index.geojson from the SunMint Tree Planting sheet.

Treasury-cache pattern: data + generator + workflow live together in the
sunmint data repo. Reads the sheet via GOOGLE_SERVICE_ACCOUNT_JSON, emits
trees/index.geojson (FeatureCollection). Run by .github/workflows/rebuild-tree-index.yml.

Usage:
    GOOGLE_SERVICE_ACCOUNT_JSON=<json> python3 scripts/build_tree_geojson.py [--out trees/index.geojson]
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
import urllib.request

SHEET_ID = "1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ"
SHEET_TAB = "SunMint Tree Planting"

# Ask-1: associate each tree with a plot at BUILD time. The sheet's "Plot ID"
# column stays an explicit OVERRIDE (it wins when present). Otherwise the tree is
# graded by geometry against the plot registry (plots/index.geojson):
#   inside  -- point-in-polygon            -> auto-associated (plot_id derived).
#   near    -- within PLOT_NEAR_METRES of a boundary -> FLAGGED, never auto-linked.
#   none    -- otherwise (correct for distributed student plantings, e.g. CFR).
PLOT_NEAR_METRES = 50.0

# Option B program attribution: a tree belongs to a lineage-credentials program
# when the host serving its submission matches one of that program's registered
# domains. The registry is the single source of truth; add a domain->slug pair
# there (never here) when a new program vendors the SunMint app.
PROGRAM_REGISTRY_URL = (
    "https://raw.githubusercontent.com/TrueSightDAO/lineage-engine/main/"
    "scripts/sunmint_program_registry.json"
)
_URL_HOST_RE = re.compile(r"^[a-zA-Z][\w+.-]*://([^/?#]+)")
_SUBMISSION_SOURCE_RE = re.compile(
    r"^[ \t]*-?[ \t]*Submission Source:[ \t]*(\S+)[ \t]*$", re.M | re.I
)
_SUBMISSION_SOURCE_INLINE_RE = re.compile(r"Submission Source:[ \t]*([^\s\\]+)", re.I)
_GENERATED_USING_RE = re.compile(r"generated using[ \t]+(\S+)", re.I)
# Personal-key handle: every submission carries the signer's RSA SPKI public key on a
# "My Digital Signature:" line (col F for legacy rows, a dedicated column when a
# writer supplies one). The per-tree pk_hash is a CANONICAL, CONTENT-ADDRESSED handle
# -- NOT a privacy device:
#   pk-<first 12 chars of base64url(SHA-256(base64-decoded SPKI bytes))>
# It is derived from the public key, which is public by construction, so anyone holding
# that key re-derives the same value -- it hides nothing. Its job is to be a STABLE,
# COMPACT JOIN KEY for a key that otherwise arrives as a ~392-char blob in several
# encodings: we decode to DER/SPKI bytes first, so every encoding of one key collapses
# to one value and the published feed stays small. Same role as a git object id.
# It mirrors the writers' own hash (tokenomics GAS cfrSubDerivePkHash_ and dapp
# cfr-anapu/payout-registration-utils derivePkHash) so a page can filter the public
# feed to the viewer's own trees without the feed carrying the raw key. See
# agentic_ai_context/conventions/DEDUP_KEY_CONVENTION.md 2.6.
#
# Algorithm id: bump this together with any change to derive_pk_hash. The emitted value
# is currently UNPREFIXED ("pk-"). A literal "pk1-" prefix flip would orphan every
# already-stored pk_hash (QR ids, credential dirs, cached files, sheet columns) and so
# is staged in OPEN_FOLLOWUPS.md, not shipped here.
PK_HASH_SCHEME = "pk1"
_PK_RE = re.compile(r"My Digital Signature:\s*(\S+)", re.I)


def derive_pk_hash(public_key_b64):
    """Return 'pk-<12 chars of base64url(SHA-256(SPKI bytes))>' or None.

    Never raises: a malformed/absent key yields None so ingest cannot break.
    """
    try:
        key = (public_key_b64 or "").strip()
        if not key:
            return None
        raw = base64.b64decode(key, validate=True)
        # Must be a DER SEQUENCE (0x30 ...) and long enough to be an SPKI --
        # rejects truncated/garbage values so a bad cell cannot invent a handle.
        if len(raw) < 128 or raw[:1] != b"\x30":
            return None
        digest = hashlib.sha256(raw).digest()
        b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return "pk-" + b64[:12]
    except Exception:
        return None


def signature_from_contribution(contribution_text, signature_cell=None):
    """Extract the signer public key from a dedicated cell, else col F text."""
    direct = (signature_cell or "").strip()
    if direct:
        return direct
    if not contribution_text:
        return ""
    for text in (contribution_text, _unescape(contribution_text)):
        m = _PK_RE.search(text)
        if m:
            return m.group(1).strip()
    return ""


# Photo URLs in the sheet are sometimes stored as github.com web-UI links
# (github.com/TrueSightDAO/sunmint/tree/main/... or /blob/...), which a browser
# <img> cannot render (they return HTML). Normalize to raw.githubusercontent.com.
def normalize_photo_url(url):
    if not url:
        return None
    u = url.strip()
    u = re.sub(
        r"^https?://github\.com/TrueSightDAO/sunmint/(?:tree|blob)/main/",
        "https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/",
        u,
    )
    return u or None


def _unescape(s):
    """Older writers stored newlines in cells as literal escaped newlines."""
    bs = chr(92)
    return s.replace(bs + "r" + bs + "n", "\n").replace(bs + "n", "\n").replace(bs + "r", "\n")


def submission_source(contribution_text):
    """Extract the raw submission origin from a Contribution Made cell (col F).

    Legacy rows have no dedicated origin column: the origin rides inside col F as a
    Submission Source line, or for older rows a trailing generated-using footer.
    Newer rows carry it in col U, which the caller prefers. Returns '' when absent.
    """
    if not contribution_text:
        return ""
    for rx in (
        _SUBMISSION_SOURCE_RE,
        _GENERATED_USING_RE,
        _SUBMISSION_SOURCE_INLINE_RE,
    ):
        for text in (contribution_text, _unescape(contribution_text)):
            m = rx.search(text)
            if m:
                return m.group(1).strip().rstrip(".")
    return ""


def source_host(value):
    """Normalise a Submission Source to a lowercase host (URLs) or sentinel.

    Mirrors lineage-engine/scripts/sync_sunmint_program_activity.py:source_host so
    the two program attributions agree. A non-URL value is returned lowercased,
    which simply fails the host-registry lookup instead of raising.
    """
    if not value:
        return ""
    m = _URL_HOST_RE.match(value.strip())
    if m:
        return m.group(1).lower()
    return value.strip().lower()


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_registry(url=None, fetch=_fetch_json):
    """Return {host: program_slug}. Best-effort: {} when the registry is unavailable."""
    try:
        payload = fetch(url or PROGRAM_REGISTRY_URL) or {}
    except Exception as exc:  # noqa: BLE001 - a missing registry must not fail the build
        print(f"[warn] program registry unavailable ({exc}); program will be ''")
        return {}
    hosts = payload.get("hosts") or {}
    return {str(k).lower(): str(v) for k, v in hosts.items()}


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


def idx(header, *names):
    # Column matching with needle priority + exact-before-prefix.
    # For each needle (most specific first), scan ALL headers for an EXACT
    # match, then a PREFIX match. This prevents substrings from misfiring:
    # e.g. "planted" must NOT hit "Photo of Tree Planted" (col 9) when we
    # want "Tree Planting Time" (col 17) -- the "tree planting time" needle
    # wins first. "status" must hit "Status" exactly, not "Status date".
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


def pick_tree_id(update_id, message_id):
    """Return the canonical tree id for a sheet row.

    The sheet carries TWO id columns: A "Telegram Update ID" and D "Telegram
    Message ID". For Edgar-direct submissions they differ by one (A = D + 1)
    because each submission writes two ledger rows -- the original and its
    echo. The CANONICAL id -- what Edgar, the [TREE PLANTING EVENT], the
    certificate's ledger_ref and the signed public attestation all key on --
    is the Telegram MESSAGE id (col D), so prefer the Edgar_*-shaped value
    from col D. Legacy Telegram-native rows carry numeric ids in BOTH columns
    (neither Edgar_*); there col A remains the id of record, so fall back to
    it. See OPEN_FOLLOWUPS.md "tree_id off-by-one" (thread 35189).
    """
    d = (message_id or "").strip()
    a = (update_id or "").strip()
    if d.startswith("Edgar_"):
        return d
    if a.startswith("Edgar_"):
        return a
    return a or d or None


def load_trees(ws, registry=None):
    registry = registry or {}
    rows = ws.get_all_values()
    if not rows:
        return []
    header = rows[0]
    c_id = idx(header, "telegram update id", "tree id")
    c_msg = idx(header, "telegram message id")
    c_contrib = idx(header, "contribution made", "contribution")
    c_sig = idx(header, "my digital signature", "digital signature", "signature")
    c_source = idx(header, "submission source")
    c_txid = idx(header, "request_transaction_id", "request transaction id", "request_txid")
    c_species = idx(header, "specie", "species")
    c_lat = idx(header, "latitude")
    c_lng = idx(header, "longitude")
    c_photo = idx(header, "photo of tree planted", "photo of tree", "photo")
    c_status = idx(header, "status")
    c_qr = idx(header, "linked qr code", "linked qr", "qr code")
    c_time = idx(header, "tree planting time", "planting time", "planted at")
    c_plot = idx(header, "plot id", "plot", "parcel", "site name", "site")
    if c_id is None:
        sys.exit("could not find tree id column")
    trees = []
    for row in rows[1:]:
        if not any((v or "").strip() for v in row):
            continue
        tid = pick_tree_id(cell(row, c_id), cell(row, c_msg))
        if not tid:
            continue
        # Skip test / E2E rows
        tl = tid.lower()
        if "e2e" in tl or "test" in tl:
            continue
        lat = cell(row, c_lat)
        lng = cell(row, c_lng)

        def to_float(v):
            try:
                return float(v.replace(",", ".")) if v else None
            except (ValueError, AttributeError):
                return None

        status = cell(row, c_status) or "NEW"
        # Rejected/invalid trees stay in the sheet as audit history but must
        # NOT appear in the public index (governor reject flow). The monitor
        # page loads index.geojson, so an INVALID row here is exactly what
        # makes a rejected tree "reappear" on reload.
        if str(status).strip().upper() == "INVALID":
            continue
        # Origin: prefer the dedicated col U ("Submission Source"), falling back to
        # parsing col F for rows written before that column existed.
        src = cell(row, c_source) or submission_source(cell(row, c_contrib))
        trees.append(
            {
                "id": tid,
                "pk_hash": derive_pk_hash(
                    signature_from_contribution(cell(row, c_contrib), cell(row, c_sig))
                ),
                "species": cell(row, c_species) or "unknown",
                "lat": to_float(lat),
                "lng": to_float(lng),
                "photo": normalize_photo_url(cell(row, c_photo)),
                "status": status,
                "qr_code": cell(row, c_qr) or None,
                "plot_id": cell(row, c_plot) or None,
                "planted_at": cell(row, c_time) or None,
                "planting_time": cell(row, c_time) or None,
                "request_txid": cell(row, c_txid) or None,
                "submission_source": src or None,
                "program": registry.get(source_host(src), ""),
            }
        )
    return trees


def _point_in_ring(x, y, ring):
    """Ray-casting point-in-polygon; ring is a closed [[lng, lat], ...] list."""
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _point_to_ring_m(lng, lat, ring):
    """Approx distance (m) from a point to the nearest ring edge (segment)."""
    kx = 111320.0 * math.cos(math.radians(lat))
    ky = 110540.0
    px, py = lng * kx, lat * ky
    best = float("inf")
    for i in range(len(ring) - 1):
        ax, ay = ring[i][0] * kx, ring[i][1] * ky
        bx, by = ring[i + 1][0] * kx, ring[i + 1][1] * ky
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = (
            0.0
            if seg <= 0
            else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        )
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        best = min(best, d)
    return best


def load_plot_rings(path):
    """Read plots/index.geojson (the ONE plot registry) -> [(plot_id, ring)].

    Returns [] on any failure: association is an ENRICHMENT, never a reason to
    fail a tree build. Only Polygon features with a plot_id and a ring qualify.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            gj = json.load(fh)
    except Exception:  # noqa: BLE001 - association is an enrichment, never fatal
        return []
    rings = []
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        ring = (geom.get("coordinates") or [[]])[0]
        pid = (feat.get("properties") or {}).get("plot_id")
        if pid and ring:
            rings.append((pid, ring))
    return rings


def associate_plot(lng, lat, rings, explicit=None, near_m=PLOT_NEAR_METRES):
    """Grade a tree's plot association against the plot rings.

    `plot_assoc` is the GEOMETRIC grade (inside | near | none). `plot_id` is the
    resolved link: the explicit sheet value when present, else the enclosing
    plot's id. A `near` tree is flagged via nearest_plot_id/plot_dist_m but NOT
    auto-linked -- proximity within the tolerance is a review prompt, not a fact.
    """
    inside_pid = None
    best_pid = None
    best_d = None
    for pid, ring in rings:
        if _point_in_ring(lng, lat, ring):
            inside_pid = pid
            best_pid, best_d = pid, 0.0
            break
        d = _point_to_ring_m(lng, lat, ring)
        if best_d is None or d < best_d:
            best_pid, best_d = pid, d
    if inside_pid:
        assoc = "inside"
    elif best_d is not None and best_d <= near_m:
        assoc = "near"
    else:
        assoc = "none"
    return {
        "plot_id": explicit or inside_pid,
        "plot_assoc": assoc,
        "plot_dist_m": round(best_d, 1) if best_d is not None else None,
        "nearest_plot_id": best_pid,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="trees/index.geojson")
    ap.add_argument("--plots", default="plots/index.geojson")
    args = ap.parse_args()
    ws = get_sheet()
    registry = load_registry()
    print(f"[info] program registry: {registry}")
    trees = load_trees(ws, registry)
    # Dedupe by tree id: the ledger can hold 2-3 rows per submission (async
    # re-scans of the same chat-log event), so emit ONE feature per unique
    # tree id, preferring the row that carries coordinates.
    seen = {}
    for t in trees:
        cur = seen.get(t["id"])
        if cur is None:
            seen[t["id"]] = t
            continue
        if cur["lat"] is None and t["lat"] is not None:
            seen[t["id"]] = t
    trees = list(seen.values())
    # Ask-1: grade every tree against the plot registry (build-time association).
    rings = load_plot_rings(args.plots)
    if not rings:
        print(
            f"WARN: plot registry '{args.plots}' unreadable/empty -- "
            "plot association omitted from this build"
        )
    features = []
    for t in trees:
        props = {
            "tree_id": t["id"],
            "species": t["species"],
            "last_measured": t["planted_at"],
            "photo_url": t["photo"],
            "status": t["status"],
            "qr_code": t["qr_code"],
            "plot_id": t.get("plot_id"),
            "submission_source": t.get("submission_source"),
            "pk_hash": t.get("pk_hash"),
            "program": t.get("program"),
            "request_txid": t.get("request_txid"),
        }
        if rings and t.get("lat") is not None and t.get("lng") is not None:
            props.update(
                associate_plot(t["lng"], t["lat"], rings, explicit=t.get("plot_id"))
            )
        props = {k: v for k, v in props.items() if v is not None}
        if t["lat"] is not None and t["lng"] is not None:
            geom = {"type": "Point", "coordinates": [t["lng"], t["lat"]]}
        else:
            geom = None  # no coordinates -> cannot be distance-ranked
        features.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": props,
            }
        )
    out = {
        "type": "FeatureCollection",
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "features": features,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(features)} features to {args.out}")
    # NOTE: plot boundaries are maintained in plots/index.geojson by
    # scripts/build_plots_geojson.py (source: "SunMint Plots" tab). The tree
    # index intentionally does NOT emit a plot layer -- see the repo README
    # ("Single source of truth for plots"). Do not reintroduce a side-car
    # plots output here; it caused file confusion (trees/plots.geojson vs
    # plots/index.geojson) and silently skipped plot-level satellite caching.


if __name__ == "__main__":
    main()
