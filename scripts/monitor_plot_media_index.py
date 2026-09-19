"""Staleness monitor for plots/media.json (plan section 6a).

Alerts when the per-plot media index is older than 2x its reconcile cadence --
the same shape as the existing df-alert.sh / map_drain_monitor.py cron entries.
Silent staleness is the enemy: a dead rebuild workflow must page, not rot.

Exit codes:
  0  fresh
  1  stale / missing / unreadable  (CRON ALERTS ON NON-ZERO)

Usage:
  python3 scripts/monitor_plot_media_index.py --index plots/media.json
Run it from cron on the autopilot box; it is also safe to call from CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_plot_media_index import CADENCE_MINUTES, check_staleness


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", default="plots/media.json")
    ap.add_argument("--url", help="fetch the index from this URL instead of --index")
    ap.add_argument("--cadence-minutes", type=int, default=CADENCE_MINUTES)
    args = ap.parse_args(argv)

    source = args.url or args.index
    try:
        if args.url:
            with urllib.request.urlopen(args.url, timeout=30) as resp:
                index = json.loads(resp.read().decode("utf-8"))
        else:
            with open(args.index) as fh:
                index = json.load(fh)
    except (OSError, ValueError) as exc:
        print(
            f"ALERT: per-plot media index unreadable at {source}: {exc}",
            file=sys.stderr,
        )
        return 1

    alert = check_staleness(index, args.cadence_minutes)
    if alert:
        print(f"ALERT: {alert}", file=sys.stderr)
        return 1
    print(f"OK: {source} fresh (generated_at {index.get('generated_at')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
