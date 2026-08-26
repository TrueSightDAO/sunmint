#!/usr/bin/env python3
"""Build sunmint/trees/index.geojson from the SunMint Tree Planting sheet.

Reads the authoritative Google Sheet (source of truth), emits a GeoJSON
FeatureCollection with one Feature per REAL tree (skips E2E/test rows),
and writes it to <repo>/trees/index.geojson for the given output dirs.

Usage:
  build_tree_geojson.py --creds <sa_key.json> --out <dir> [--out <dir2> ...]
  (each --out receives trees/index.geojson)
"""
import argparse, json, os, re, sys
from datetime import datetime, timezone


def parse_sheet_values(values):
    """values: list of rows from the 'SunMint Tree Planting' tab (header first)."""
    if not values:
        return []
    header = [str(h).strip().lower() for h in values[0]]
    col = {name: i for i, name in enumerate(header)}
    def idx(*names):
        for n in names:
            if n in col:
                return col[n]
        return None
    c_id = idx('telegram update id', 'telegram update id', 'update id')
    c_species = idx('specie', 'species')
    c_lat = idx('latitude', 'lat')
    c_lng = idx('longitude', 'lng', 'lon')
    c_photo = idx('photo of tree planted', 'photo url', 'photo')
    c_status = idx('status')
    c_qr = idx('linked qr code', 'qr code')
    c_time = idx('tree planting time', 'planting time')

    trees = []
    for row in values[1:]:
        while len(row) <= max(filter(None, [c_id, c_species, c_lat, c_lng, c_photo, c_status, c_qr, c_time])):
            row.append('')
        def cell(i):
            return row[i].strip() if (i is not None and i < len(row)) else ''

        rid = cell(c_id)
        # skip headers / empty rows
        if not rid or rid.lower().startswith('telegram'):
            continue
        # skip E2E / test rows
        if re.search(r'test|e2e', rid, re.I) or re.search(r'test|e2e', cell(c_species), re.I):
            continue

        lat_s, lng_s = cell(c_lat).replace(',', '.'), cell(c_lng).replace(',', '.')
        lat = float(lat_s) if re.match(r'^-?\d+(\.\d+)?$', lat_s) else None
        lng = float(lng_s) if re.match(r'^-?\d+(\.\d+)?$', lng_s) else None

        tree = {
            'id': rid,
            'species': cell(c_species) or 'unknown',
            'photo': cell(c_photo) or None,
            'status': cell(c_status) or 'NEW',
            'qr_code': cell(c_qr) or None,
            'planted_at': cell(c_time) or None,
            'lat': lat,
            'lng': lng,
        }
        trees.append(tree)
    return trees


def build_geojson(trees):
    features = []
    for t in trees:
        props = {
            'tree_id': t['id'],
            'species': t['species'],
            'last_measured': t['planted_at'],
            'photo_url': t['photo'],
            'status': t['status'],
            'qr_code': t['qr_code'],
        }
        props = {k: v for k, v in props.items() if v is not None}
        if t['lat'] is not None and t['lng'] is not None:
            geom = {'type': 'Point', 'coordinates': [t['lng'], t['lat']]}
        else:
            geom = None  # no coordinates -> cannot be distance-ranked
        features.append({
            'type': 'Feature',
            'properties': props,
            'geometry': geom,
        })
    fc = {
        'type': 'FeatureCollection',
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'features': features,
    }
    return fc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--creds', required=True)
    ap.add_argument('--out', action='append', required=True,
                    help='output dir to write trees/index.geojson into (repeatable)')
    args = ap.parse_args()

    import gspread
    from google.oauth2 import service_account
    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
    creds = service_account.Credentials.from_service_account_file(args.creds, scopes=scopes)
    gc = gspread.authorize(creds)

    sheet = gc.open_by_key('1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ')
    ws = sheet.worksheet('SunMint Tree Planting')
    values = ws.get_all_values()

    trees = parse_sheet_values(values)
    fc = build_geojson(trees)

    for outdir in args.out:
        os.makedirs(os.path.join(outdir, 'trees'), exist_ok=True)
        path = os.path.join(outdir, 'trees', 'index.geojson')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(fc, f, indent=2, ensure_ascii=False)
        print(f'wrote {path}: {len(fc["features"])} features '
              f'({sum(1 for t in trees if t["lat"] is not None and t["lng"] is not None)} with coords)')

    # summary
    print(f'\nTotal real trees: {len(trees)}')
    print('Trees WITHOUT coordinates (cannot be distance-ranked, need manual ID):')
    for t in trees:
        if t['lat'] is None or t['lng'] is None:
            print(f'  - {t["id"]} ({t["species"]})')


if __name__ == '__main__':
    main()
