"""Build the SunMint farms seed (farms/index.json).

Mirrors scripts/build_plots_geojson.py. Reads the "SunMint Plots" tab of the
SunMint ledger spreadsheet and emits a machine-generated farms index that the
farmer app (sunmint_beta) fetches to seed the farm dropdown, unioned with the
device-local farm list (SUNMINT_BOUNDARY_SUBMISSION_PLAN rules 1-3).

SAFETY: mirrors the plots generator -- if the tab cannot be read (auth /
permission / network), FAIL LOUDLY (non-zero exit) and leave farms/index.json
UNTOUCHED. The old silent-green "preserve the existing index / exit 0" branch hid
the 2026-09-17 -> 09-24 SunMint index freeze for 7 days (see OPEN_FOLLOWUPS.md ->
SunMint index freeze).

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


def sheet_read_failure(tab, reason):
    """Loud-failure message for a sheet we could not read (silent-green ban, 2026-09-24).

    A generator that cannot read its source must FAIL and force a non-zero exit;
    the old swallow-and-preserve branch rewrote the previous file and exited 0.
    """
    cred = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    hint = (
        "" if cred else " [GOOGLE_SERVICE_ACCOUNT_JSON is unset -- set the CI secret]"
    )
    return (
        f"ERROR: could not read '{tab}' tab: {reason}{hint} -- refusing to silently "
        "preserve the existing farms index (see OPEN_FOLLOWUPS.md -> SunMint index freeze)"
    )


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
        # Empty response from a sheet that should have a header = read failure.
        sys.exit(sheet_read_failure(SHEET_TAB, "tab returned no rows (empty response)"))
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
    except Exception as e:  # noqa: BLE001 -- any read failure MUST fail the job, loudly
        # Silent-green ban (2026-09-24): never preserve-and-exit-0 on a read error.
        sys.exit(sheet_read_failure(SHEET_TAB, f"{type(e).__name__}: {e}"))

    if not farms:
        # Header with zero farm rows: fail loudly rather than publish an empty index
        # or silently preserve. The existing file is left untouched.
        sys.exit(sheet_read_failure(SHEET_TAB, "no farm rows after parsing"))

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
