from __future__ import annotations

import pytest
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from hyptop_dashboard.cli import _update_metrics
from hyptop_dashboard.parser import LparRow, real_smt_utilization_percent


def _gauge_value(text: bytes, metric: str, system: str) -> float | None:
    for family in text_string_to_metric_families(text.decode()):
        if family.name != metric:
            continue
        for sample in family.samples:
            if sample.name == metric and sample.labels.get("system") == system:
                return float(sample.value)
    return None


def test_per_num_cores_metrics_from_fixture_row():
    rows = [
        LparRow("S35LP41", 12, 24, 101.28, 170.28, 0.28),
    ]
    s = 1.3
    _update_metrics(rows, s)
    text = generate_latest()
    u_r = real_smt_utilization_percent(101.28, 170.28, 0.28, s)
    assert pytest.approx(_gauge_value(text, "hyptop_lpar_core_per_num_cores_hyptop_percent", "S35LP41")) == 101.28 / 12
    assert pytest.approx(_gauge_value(text, "hyptop_lpar_real_smt_per_num_cores_hyptop_percent", "S35LP41")) == u_r / 12


def test_per_num_cores_metrics_zvm_row():
    rows = [LparRow("G3545010", 3, 3, 0.55, 0.55, 0.05)]
    s = 1.3
    _update_metrics(rows, s)
    text = generate_latest()
    u_r = real_smt_utilization_percent(0.55, 0.55, 0.05, s)
    assert pytest.approx(_gauge_value(text, "hyptop_lpar_core_per_num_cores_hyptop_percent", "G3545010")) == 0.55 / 3
    assert pytest.approx(_gauge_value(text, "hyptop_lpar_real_smt_per_num_cores_hyptop_percent", "G3545010")) == u_r / 3
