"""Regression test: the SunMint index generators FAIL LOUDLY on a sheet-read error.

Silent-green ban (2026-09-24). Before this fix, build_plots_geojson.py and
build_farms_index.py caught a sheet-read failure, logged a WARN, rewrote the
previous file, committed nothing and EXITED 0 -- so a transient credential /
permission failure read as success and the public indexes froze for 7 days
(2026-09-17 -> 09-24). These tests pin the loud-failure contract:

  * a read error must raise SystemExit with a NON-ZERO code,
  * the existing output file must be left byte-for-byte untouched.

They FAIL on the pre-fix code (which exited 0 and preserved), and PASS after.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_farms_index as bf
import build_plots_geojson as bp

SENTINEL = '{"sentinel": "must-not-be-clobbered"}'


class _RaisingWS:
    """A worksheet whose read blows up the way a revoked service account does (403)."""

    def __init__(self, exc):
        self._exc = exc

    def get_all_values(self):
        raise self._exc


class _EmptyWS:
    """A sheet that returns nothing -- a read failure, not an empty registry."""

    def get_all_values(self):
        return []


def _assert_loud_exit(monkeypatch, module, out_path):
    monkeypatch.setattr(sys, "argv", ["prog", "--out", out_path])
    with pytest.raises(SystemExit) as ei:
        module.main()
    assert ei.value.code not in (0, None), "a read failure must exit non-zero"


@pytest.mark.parametrize(
    "ws", [_RaisingWS(PermissionError("403 Forbidden")), _EmptyWS()]
)
def test_plots_fails_loud_and_preserves_existing(monkeypatch, tmp_path, ws):
    out = tmp_path / "index.geojson"
    out.write_text(SENTINEL)
    monkeypatch.setattr(bp, "get_sheet", lambda: ws)
    _assert_loud_exit(monkeypatch, bp, str(out))
    assert out.read_text() == SENTINEL, "the fail path must not touch the existing file"


@pytest.mark.parametrize(
    "ws", [_RaisingWS(PermissionError("403 Forbidden")), _EmptyWS()]
)
def test_farms_fails_loud_and_preserves_existing(monkeypatch, tmp_path, ws):
    out = tmp_path / "index.json"
    out.write_text(SENTINEL)
    monkeypatch.setattr(bf, "get_sheet", lambda: ws)
    _assert_loud_exit(monkeypatch, bf, str(out))
    assert out.read_text() == SENTINEL, "the fail path must not touch the existing file"
