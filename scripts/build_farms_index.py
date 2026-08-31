"""Build the SunMint farms seed index (farms/index.json).

Mirrors scripts/build_plots_geojson.py. Reads the "SunMint Plots" tab of the
SunMint ledger spreadsheet and emits the deduplicated farm list the farmer app
uses to seed its farm dropdown (rules 1-3 of SUNMINT_BOUNDARY_SUBMISSION_PLAN:
a farm is selectable even before its plot record exists, via this seed +
device-local names).

SAFETY: if the tab is missing or has no rows, PRESERVE the existing
farms/index.json instead of clobbering it with an empty list.

Usage:
  python3 scripts/build_farms_index.py [--out farms/index.json]
"""

import argparse
import datetime
import json
import os
import sys

SHEET_ID = "1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ"
SHEET_TAB = "SunMint Plots"


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


def slugify(name):
    """Normalize a farm name to a stable, comparable slug."""
    if not name:
        return ""
    s = name.strip().lower()
    s = "".join(c if c.isalnum() else "-" for c in s)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def humanize(name):
    """Best-effort display name: title-case words joined by spaces."""
    if not name:
        return ""
    words = [w for w in name.replace("-", " ").replace("_", " ").split() if w]
    if not words:
        return ""
    return " ".join(w[0].upper() + w[1:] if w else w for w in words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="farms/index.json")
    args = ap.parse_args()

    try:
        ws = get_sheet()
        rows = ws.get_all_values()
    except (Exception, SystemExit) as e:
        print(
            f"WARN: could not read '{SHEET_TAB}' tab ({e}); preserving existing registry"
        )
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"preserved {len(existing.get('farms', []))} farms at {args.out}")
            return
        sys.exit(f"no source tab and no existing {args.out} to preserve")

    if not rows:
        print("WARN: 'SunMint Plots' tab has no rows; preserving existing registry")
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"preserved {len(existing.get('farms', []))} farms at {args.out}")
            return
        sys.exit(f"no rows and no existing {args.out} to preserve")

    header = rows[0]
    col_farm = idx(header, ["farm id", "farm"])

    farms = {}  # slug -> display name
    for row in rows[1:]:
        if not any((v or "").strip() for v in row):
            continue
        farm = cell(row, col_farm)
        if not farm:
            continue
        slug = slugify(farm)
        if not slug:
            continue
        if slug not in farms:
            farms[slug] = humanize(farm.strip()) or farm.strip()

    out = {
        "farms": [{"id": slug, "name": name} for slug, name in sorted(farms.items())],
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "count": len(farms),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(farms)} farms to {args.out}")


if __name__ == "__main__":
    main()
