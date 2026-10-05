"""
NMEA parsing in run_viewer.py: real sentences accepted, and every failure mode found in the
2026-10-04 review rejected (cut-off/split sentences, logger prefixes, NaN/inf, dropped leading
zeros, out-of-range values, simulator/invalid modes). Stdlib + pytest only:

    cd Viewer3D_NewLayout && python -m pytest -q tests/test_nmea.py
"""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_viewer as rv  # noqa: E402


def cs(body):
    """'$'+body+'*hh' with a correct checksum."""
    x = 0
    for ch in body:
        x ^= ord(ch)
    return f"${body}*{x:02X}"


@pytest.fixture(autouse=True)
def default_policy():
    rv.POLICY.update(require_checksum=True, plain_feed=False)
    yield
    rv.POLICY.update(require_checksum=True, plain_feed=False)


# ---------------------------------------------------------------- accepted (from the real feed)
def test_real_feed_sentences():
    rmc = "$GPRMC,000715.03,A,0658.813312,N,07955.163369,W,13.1,233.95,051026,,,D*43"
    vtg = "$GPVTG,233.95,T,,M,13.1,N,24.3,K,D*30"
    la, lo, utc, k = rv.parse_position(rmc)
    assert k == "RMC" and abs(la - (6 + 58.813312 / 60)) < 1e-9 and abs(lo + (79 + 55.163369 / 60)) < 1e-9
    assert rv.parse_motion(rmc)[:2] == (233.95, 13.1)
    assert rv.parse_motion(vtg)[:2] == (233.95, 13.1)
    assert rv.parse_position("$GPZDA,000717.03,05,10,2026,,*66") is None


def test_gga_hemispheres_and_talkers():
    for talker in ("GP", "GN", "IN"):
        r = rv.parse_position(cs(f"{talker}GGA,123519,0042.1716,S,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"))
        assert r and abs(r[0] + 0.702860) < 1e-6 and abs(r[1] + 91.208333) < 1e-6


def test_heading_sentences():
    assert rv.parse_heading(cs("HEHDT,274.1,T")) == (274.1, True, "HDT")
    assert rv.parse_heading(cs("INTHS,12.5,A"))[:2] == (12.5, True)
    # HDG with deviation 1.0 E and variation 2.5 E -> true 273.5
    deg, true, _ = rv.parse_heading(cs("HCHDG,270.0,1.0,E,2.5,E"))
    assert true and abs(deg - 273.5) < 1e-9
    deg, true, _ = rv.parse_heading(cs("HCHDG,270.0,,,,"))
    assert not true and deg == 270.0


def test_gll_nmea1_without_status_is_accepted():
    assert rv.parse_position(cs("GPGLL,0042.1716,N,09112.5000,W")) is not None


def test_logger_prefix_is_stripped_not_misread():
    r = rv.parse_position("12:00:00.123 " + cs("GPGGA,123519,0042.1716,N,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"))
    assert r and abs(r[0] - 0.70286) < 1e-5


# ---------------------------------------------------------------- rejected
@pytest.mark.parametrize("line", [
    ",1,08,0.9,5.4,M,9.9,M,,*47",          # tail of a split GGA (was read as 1N 8E)
    "M,46.9,M,,*47",
    "12:00:00.123",                         # timestamp alone (was 12N 0E)
    "$GPGGA,123519,0042.17",                # cut off, no checksum
    cs("GPGGA,123519,00nan,S,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"),
    cs("GPGGA,123519,9999.9900,N,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"),   # lat > 90
    cs("GPGGA,123519,0061.0000,N,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"),   # minutes >= 60
    cs("GPGGA,123519,0000.0000,N,00000.0000,E,1,08,0.9,5.4,M,9.9,M,,"),   # null island
    cs("GPGGA,123519,0042.1716,N,09112.5000,W,8,08,0.9,5.4,M,9.9,M,,"),   # simulator
    cs("GPGGA,123519,0042.1716,N,09112.5000,W,0,08,0.9,5.4,M,9.9,M,,"),   # no fix
    cs("GPRMC,123519,V,0042.1716,N,09112.5000,W,0.0,0.0,051026,,,A"),     # void
    cs("GPRMC,123519,A,0042.1716,N,09112.5000,W,0.0,0.0,051026,,,N"),     # mode not valid
    cs("GPGGA,123519,0042.1716,X,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"),   # bad hemisphere
])
def test_bad_positions_rejected(line):
    assert rv.parse_position(line) is None


def test_dropped_leading_zeros_parse_correctly():
    r = rv.parse_position(cs("GPGGA,123519,42.1234,S,9112.5000,W,1,08,0.9,5.4,M,9.9,M,,"))
    assert r and abs(r[0] + 0.702057) < 1e-6 and abs(r[1] + 91.208333) < 1e-6


@pytest.mark.parametrize("line", [
    ",T*20", "T*2B", "gyro error 5", "$HEHDT,27", cs("HEHDT,nan,T"), cs("HEHDT,1e999,T"), cs("HEHDT,400,T"),
    cs("INTHS,12.5,S"), cs("INTHS,12.5,V"),
])
def test_bad_headings_rejected(line):
    assert rv.parse_heading(line) is None


@pytest.mark.parametrize("line", [cs("GPVTG,inf,T,,M,5.5,N,10.2,K,A"), cs("GPVTG,54.7,T,,M,5.5,N,10.2,K,N"),
                                  "$GPRMC,123519,A,0042.1716,N,09112.5000,W,13.1,12"])
def test_bad_motion_rejected(line):
    assert rv.parse_motion(line) is None


def test_plain_feed_is_opt_in_and_whole_line_only():
    assert rv.parse_position("0.7029 -91.2083") is None
    rv.POLICY["plain_feed"] = True
    assert rv.parse_position("0.7029 -91.2083")[:2] == (0.7029, -91.2083)
    assert rv.parse_position("fix 0.7029 -91.2083 ok") is None
    assert rv.parse_heading("274.5")[:2] == (274.5, True)
    assert rv.parse_heading("gyro error 5") is None


def test_no_checksum_only_when_allowed():
    line = "$GPGGA,123519,0042.1716,N,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"
    assert rv.parse_position(line) is None
    rv.POLICY["require_checksum"] = False
    assert rv.parse_position(line) is not None


# ---------------------------------------------------------------- state / JSON
def test_snapshot_is_strict_json_and_ages_from_monotonic_clock():
    ship = rv.ShipState()
    rv._handle_line(cs("GPGGA,123519,0042.1716,N,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"), "gps", ship)
    rv._handle_line(cs("GPGGA,123519,00nan,N,09112.5000,W,1,08,0.9,5.4,M,9.9,M,,"), "gps", ship)
    rv._handle_line("garbage \x00\xff", "gps", ship)
    snap = ship.snapshot(True)
    json.dumps(snap, allow_nan=False)  # raises if any NaN/inf got through
    assert snap["position"]["age_s"] >= 0 and "mono" not in snap["position"]
    assert snap["counts"]["ignored"] == 2 and all(math.isfinite(v) for p in snap["track"] for v in p)


def test_seen_dictionary_is_bounded():
    ship = rv.ShipState()
    for i in range(500):
        rv._handle_line(f"$X{i:05d},1*00", "gps", ship)
    assert len(ship.seen["gps"]) <= 40
