"""Tests for scripts/monitor_plot_media_index.py."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import build_plot_media_index as b
import monitor_plot_media_index as mon


def _stamp(minutes_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        minutes=minutes_ago
    )
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_check_staleness_fresh():
    assert b.check_staleness({"generated_at": _stamp(5)}, 15) is None


def test_check_staleness_overdue():
    alert = b.check_staleness({"generated_at": _stamp(45)}, 15)
    assert alert and "STALE" in alert


def test_check_staleness_missing_and_unparsable():
    assert "no generated_at" in b.check_staleness({}, 15)
    assert "unparsable" in b.check_staleness({"generated_at": "not-a-date"}, 15)


def test_monitor_exit_codes(tmp_path, capsys):
    idx = tmp_path / "media.json"
    assert mon.main(["--index", str(idx)]) == 1  # missing
    idx.write_text(json.dumps({"generated_at": _stamp(1)}))
    assert mon.main(["--index", str(idx)]) == 0
    idx.write_text(json.dumps({"generated_at": _stamp(120)}))
    assert mon.main(["--index", str(idx)]) == 1
    idx.write_text("{not json")
    assert mon.main(["--index", str(idx)]) == 1
