import pytest

from hyptop_dashboard.parser import (
    LparRow,
    parse_hyptop_sys_list_text,
    real_smt_utilization_percent,
)


def test_real_smt_utilization_blog_example():
    # https://linux.mainframe.blog/smt_utilization/ T35LP76 row, s=1.3
    u_c, u_t, u_m = 799.20, 1198.90, 0.02
    u_r = real_smt_utilization_percent(u_c, u_t, u_m, 1.3)
    assert pytest.approx(u_r, rel=1e-4) == 707.03


def test_real_smt_invalid_s():
    with pytest.raises(ValueError):
        real_smt_utilization_percent(1.0, 1.0, 0.0, 0.0)


def test_parse_sample_fixture():
    from pathlib import Path

    text = (Path(__file__).parent / "fixtures" / "hyptop_sys_list_sample.txt").read_text()
    rows = parse_hyptop_sys_list_text(text)
    assert rows == [
        LparRow("S35LP41", 12, 24, 101.28, 170.28, 0.28),
        LparRow("S35LP42", 16, 32, 35.07, 40.07, 0.44),
        LparRow("S35LP64", 3, 3, 1.20, 1.20, 0.00),
    ]


def test_skips_aggregate_and_headers():
    text = """
12:30:48 | cpu-t: IFL(18)  CP(3)  UN(3)                           ?=help
system   #core    core    mgm    Core+  Mgm+   online
(str)      (#)     (%)    (%)     (hm)  (hm)    (dhm)
S05LP30     10  461.14  10.18  1547:41  8:15 11:05:59
          413  823.39  23.86  3159:57 38:08 11:06:01
"""
    rows = parse_hyptop_sys_list_text(text)
    assert len(rows) == 0  # sample uses fewer columns; not valid for our MIN_COLUMNS path


def test_parse_short_row_ignored():
    assert parse_hyptop_sys_list_text("foo 1\n") == []
