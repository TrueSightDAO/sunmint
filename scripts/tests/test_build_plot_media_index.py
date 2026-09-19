"""Tests for scripts/build_plot_media_index.py."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import build_plot_media_index as m


# --- fixtures --------------------------------------------------------------- #
def _plot(pid, farm, ring):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"plot_id": pid, "farm_id": farm, "name": f"{pid} name"},
    }


# a 1x1 degree square around (lng -52.6..-52.5, lat -3.4..-3.3)
RING = [[-52.6, -3.4], [-52.5, -3.4], [-52.5, -3.3], [-52.6, -3.3], [-52.6, -3.4]]


@pytest.fixture()
def world(tmp_path):
    plots = {"type": "FeatureCollection", "features": [_plot("A-P1", "farm-a", RING)]}
    pp = tmp_path / "index.geojson"
    pp.write_text(json.dumps(plots))
    md = tmp_path / "manifests"
    md.mkdir()
    return pp, md


def _write(d, name, doc):
    (d / name).write_text(json.dumps(doc))


# --- geometry --------------------------------------------------------------- #
def test_point_in_ring_inside_outside():
    assert m.point_in_ring(-52.55, -3.35, RING) is True
    assert m.point_in_ring(-52.9, -3.35, RING) is False
    assert m.point_in_ring(-52.55, -3.9, RING) is False


def test_point_in_ring_degenerate():
    assert m.point_in_ring(0, 0, []) is False
    assert m.point_in_ring(0, 0, [[0, 0], [1, 1]]) is False


# --- item shapes ------------------------------------------------------------ #
@pytest.mark.parametrize("key", ["items", "videos", "media"])
def test_get_items_all_shapes(key):
    assert len(m.get_items({key: [{"a": 1}, "junk"]})) == 1


def test_get_items_bare_list():
    assert len(m.get_items([{"a": 1}])) == 1


def test_get_items_bad():
    assert m.get_items({"nope": 1}) == []
    assert m.get_items("string") == []


def test_item_kind():
    assert m.item_kind({"yt_id": "x"}) == "video"
    assert m.item_kind({"file": "a.MOV"}) == "video"
    assert m.item_kind({"type": "image"}) == "image"
    assert m.item_kind({"file": "a.jpg"}) == "image"


# --- attribution ------------------------------------------------------------ #
def test_attribution_priority_plot_id_over_polygon(world):
    pp, md = world
    # lat/lng sits OUTSIDE the polygon, but explicit plot_id wins.
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "items": [
                {
                    "plot_id": "A-P1",
                    "file": "v1.mov",
                    "yt_id": "Y1",
                    "latitude": 9.9,
                    "longitude": 9.9,
                }
            ],
        },
    )
    out = m.build_payload(str(md), str(pp))
    assert out["plots"]["A-P1"]["counts"]["total"] == 1
    assert out["plots"]["A-P1"]["media"][0]["attribution"] == "plot_id"
    assert out["counts"]["unattributed"] == 0


def test_attribution_point_in_polygon(world):
    pp, md = world
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "items": [{"file": "v1.mov", "latitude": -3.35, "longitude": -52.55}],
        },
    )
    out = m.build_payload(str(md), str(pp))
    assert out["plots"]["A-P1"]["media"][0]["attribution"] == "point-in-polygon"


def test_attribution_manifest_single_plot_fallback(world):
    pp, md = world
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "plot_ids": ["A-P1"],
            "items": [{"file": "v1.mov"}],  # no gps, no plot_id
        },
    )
    out = m.build_payload(str(md), str(pp))
    assert out["plots"]["A-P1"]["media"][0]["attribution"] == "manifest-plot-id"


def test_unattributed_is_recorded_not_dropped(tmp_path, capsys):
    # TWO known plots + no gps + no item plot_id -> manifest fallback is ambiguous
    plots = {
        "type": "FeatureCollection",
        "features": [_plot("A-P1", "farm-a", RING), _plot("B-P2", "farm-a", RING)],
    }
    pp = tmp_path / "index.geojson"
    pp.write_text(json.dumps(plots))
    md = tmp_path / "manifests"
    md.mkdir()
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "plot_ids": ["A-P1", "B-P2"],
            "items": [{"file": "orphan.mov"}],
        },
    )
    out = m.build_payload(str(md), str(pp))
    assert out["counts"]["unattributed"] == 1
    assert out["unattributed"]["farm-a"][0]["ref"] == "orphan.mov"
    assert "could not be attributed" in capsys.readouterr().err


# --- freshness / idempotency ------------------------------------------------ #
def test_generated_at_absent_from_core(world, tmp_path):
    pp, md = world
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "items": [{"plot_id": "A-P1", "file": "v.mov"}],
        },
    )
    p1 = m.build_payload(str(md), str(pp))
    p2 = m.build_payload(str(md), str(pp))
    assert m._core(p1) == m._core(p2)


def test_idempotent_rerun_no_write(world, tmp_path, capsys):
    pp, md = world
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "items": [{"plot_id": "A-P1", "file": "v.mov"}],
        },
    )
    out = tmp_path / "media.json"
    rc = m.main(["--manifests-dir", str(md), "--plots", str(pp), "--out", str(out)])
    assert rc == 0
    first = out.read_text()
    assert "wrote" in capsys.readouterr().out
    rc = m.main(["--manifests-dir", str(md), "--plots", str(pp), "--out", str(out)])
    assert rc == 0
    assert "unchanged" in capsys.readouterr().out
    assert out.read_text() == first  # byte-identical -> no empty commit


def test_new_plot_geometry_retriggers_index(world, tmp_path):
    pp, md = world
    _write(
        md,
        "farm-a.json",
        {"farm_id": "farm-a", "updated": "2026-09-01", "items": [{"file": "v.mov"}]},
    )
    out = tmp_path / "media.json"
    m.main(["--manifests-dir", str(md), "--plots", str(pp), "--out", str(out)])
    before = json.loads(out.read_text())
    assert before["source"]["plots_index_sha256"]
    # simulate a NEW plot geometry with no new media -> sha changes -> re-trigger
    plots = {
        "type": "FeatureCollection",
        "features": [_plot("A-P1", "farm-a", RING), _plot("C-P9", "farm-c", RING)],
    }
    pp.write_text(json.dumps(plots))
    m.main(["--manifests-dir", str(md), "--plots", str(pp), "--out", str(out)])
    after = json.loads(out.read_text())
    assert (
        after["source"]["plots_index_sha256"] != before["source"]["plots_index_sha256"]
    )
    assert "C-P9" in after["plots"]


def test_check_detects_drift(world, tmp_path):
    pp, md = world
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-01",
            "items": [{"plot_id": "A-P1", "file": "v.mov"}],
        },
    )
    out = tmp_path / "media.json"
    m.main(["--manifests-dir", str(md), "--plots", str(pp), "--out", str(out)])
    assert (
        m.main(
            [
                "--manifests-dir",
                str(md),
                "--plots",
                str(pp),
                "--out",
                str(out),
                "--check",
            ]
        )
        == 0
    )
    # new media -> drift
    _write(
        md,
        "farm-a.json",
        {
            "farm_id": "farm-a",
            "updated": "2026-09-02",
            "items": [
                {"plot_id": "A-P1", "file": "v.mov"},
                {"plot_id": "A-P1", "file": "v2.mov"},
            ],
        },
    )
    assert (
        m.main(
            [
                "--manifests-dir",
                str(md),
                "--plots",
                str(pp),
                "--out",
                str(out),
                "--check",
            ]
        )
        == 1
    )


def test_missing_inputs_return_2(world, tmp_path):
    pp, md = world
    assert (
        m.main(
            [
                "--manifests-dir",
                str(md),
                "--plots",
                str(pp),
                "--out",
                str(tmp_path / "x.json"),
                "--check",
            ]
        )
        == 1
    )  # no out yet
    assert (
        m.main(
            [
                "--manifests-dir",
                "/nope",
                "--plots",
                str(pp),
                "--out",
                str(tmp_path / "y.json"),
            ]
        )
        == 2
    )
    assert (
        m.main(
            [
                "--manifests-dir",
                str(md),
                "--plots",
                "/nope",
                "--out",
                str(tmp_path / "z.json"),
            ]
        )
        == 2
    )


def test_staleness_empty_index_fails():
    assert m.check_staleness({"generated_at": None}, cadence_minutes=15) is not None
    assert m.check_staleness({}, cadence_minutes=15) is not None
