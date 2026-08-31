"""Build the SunMint farms seed (farms/index.json).

Mirrors scripts/build_plots_geojson.py. Reads the "SunMint Plots" tab of the
SunMint ledger spreadsheet and emits a machine-generated farms index that the
farmer app (sunmint_beta) fetches to seed the farm dropdown, unioned with the
device-local farm list (SUNMINT_BOUNDARY_SUBMISSION_PLAN rules 1-3).

SAFETY: mirrors the plots generator -- if the tab is missing or has no rows,
PRESERVE the existing farms/index.json instead of clobbering it.

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

FIELD_COLUMNS = {
    "farm_id": ["farm id", "farm"],
    "plot_id": ["plot id", "plot"],
    "name": ["plot name", "name", "site name"],
    "hectares": ["hectares", "area ha", "area"],
    "status": ["status"],
    "region": ["region", "state", "municipality"],
    "owner": ["owner", "family", "farmer"],
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


def humanize(farm_id):
    """rancho-maranta -> Rancho Maranta (for display)."""
    if not farm_id:
        return None
    return " ".join(
        w.capitalize() for w in str(farm_id).replace("-", " ").replace("_", " ").split()
    )


def load_farms(ws):
    rows = ws.get_all_values()
    if not rows:
        return []
    header = rows[0]
    cols = {f: idx(header, names) for f, names in FIELD_COLUMNS.items()}
    if cols["farm_id"] is None:
        sys.exit("could not find farm id column in 'SunMint Plots' tab")
    farms = {}
    for row in rows[1:]:
        if not any((v or "").strip() for v in row):
            continue
        fid = cell(row, cols["farm_id"])
        if not fid:
            continue
        status = cell(row, cols["status"]) or "proposed"
        if str(status).strip().upper() == "INVALID":
            continue
        entry = farms.setdefault(
            fid,
            {
                "farm_id": fid,
                "name": humanize(fid),
                "region": cell(row, cols["region"]) or None,
                "owner": cell(row, cols["owner"]) or None,
                "plot_count": 0,
                "total_hectares": 0.0,
                "statuses": {},
            },
        )
        entry["plot_count"] += 1
        ha = None
        try:
            ha = float((cell(row, cols["hectares"]) or "").replace(",", "."))
        except (ValueError, AttributeError):
            ha = None
        if ha:
            entry["total_hectares"] += ha
        entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
        if entry["region"] is None:
            entry["region"] = cell(row, cols["region"]) or None
        if entry["owner"] is None:
            entry["owner"] = cell(row, cols["owner"]) or None
    return sorted(farms.values(), key=lambda f: f["farm_id"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="farms/index.json")
    args = ap.parse_args()

    try:
        ws = get_sheet()
        farms = load_farms(ws)
    except (Exception, SystemExit) as e:
        print(
            f"WARN: could not read '{SHEET_TAB}' tab ({e}); preserving existing farms index"
        )
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"preserved {len(existing.get('farms', []))} farms at {args.out}")
            return
        sys.exit(f"no source tab and no existing {args.out} to preserve")

    if not farms:
        print(f"WARN: '{SHEET_TAB}' tab has no farms; preserving existing farms index")
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"preserved {len(existing.get('farms', []))} farms at {args.out}")
            return
        sys.exit(f"no farms and no existing {args.out} to preserve")

    out = {
        "type": "farms_index",
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "farms": farms,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(farms)} farms to {args.out}")


if __name__ == "__main__":
    main()
