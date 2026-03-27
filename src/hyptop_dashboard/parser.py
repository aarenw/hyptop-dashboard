"""Parse hyptop batch output (LPAR or z/VM sys_list)."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Literal

LOG = logging.getLogger(__name__)

HypervisorKind = Literal["lpar", "zvm"]

# IBM sys_list -f field sets (see LPAR vs z/VM field docs)
HYPTOP_FIELD_SPEC_LPAR = "#,T,c,e,m,C,E,M,o"
HYPTOP_FIELD_SPEC_ZVM = "#,c,m,C,M,o"
# Backward-compatible alias
HYPTOP_FIELD_SPEC = HYPTOP_FIELD_SPEC_LPAR

MIN_COLUMNS_LPAR = 6
MIN_COLUMNS_ZVM = 4


@dataclass(frozen=True)
class LparRow:
    """One sys_list row: LPAR name or z/VM guest id in `system`."""

    system: str
    num_cores: int
    num_threads: int
    core_percent: float
    thread_percent: float
    mgm_percent: float


def real_smt_utilization_percent(
    u_c: float,
    u_t: float,
    u_m: float,
    s: float,
) -> float:
    """
    Real SMT utilization in hyptop % units (same scale as core/thread/mgm).

    See: https://linux.mainframe.blog/smt_utilization/
    u_r = (2*u_c - u_t)/s + (u_t - u_c) + u_m
    """
    if s <= 0:
        raise ValueError("SMT speedup factor s must be positive")
    return (2.0 * u_c - u_t) / s + (u_t - u_c) + u_m


def _split_row(line: str) -> list[str]:
    return re.split(r"\s+", line.strip())


def _parse_float_hyptop(s: str) -> float:
    """Parse hyptop numeric field; '-' means unavailable (use 0)."""
    if s in ("-", ""):
        return 0.0
    return float(s)


def _is_data_row(parts: list[str], min_columns: int) -> bool:
    if len(parts) < min_columns:
        return False
    system = parts[0]
    if not system or system == "(str)":
        return False
    if re.match(r"^\d{2}:\d{2}:\d{2}", system):
        return False
    if system == "system":
        return False
    if system.isdigit():
        return False
    return True


def _parse_lpar_rows(text: str) -> list[LparRow]:
    rows: list[LparRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith("?=help"):
            continue
        parts = _split_row(line)
        if not _is_data_row(parts, MIN_COLUMNS_LPAR):
            continue
        try:
            n_core = int(parts[1])
            n_the = int(parts[2])
            u_c = float(parts[3])
            u_t = float(parts[4])
            u_m = _parse_float_hyptop(parts[5])
        except (ValueError, IndexError):
            LOG.debug("skip unparsable line: %r", line[:120])
            continue
        rows.append(
            LparRow(
                system=parts[0],
                num_cores=n_core,
                num_threads=n_the,
                core_percent=u_c,
                thread_percent=u_t,
                mgm_percent=u_m,
            )
        )
    return rows


def _parse_zvm_rows(text: str) -> list[LparRow]:
    """z/VM sys_list with -f '#,c,m,C,M,o': system, #cpu, cpu%, mgm%, ..."""
    rows: list[LparRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith("?=help"):
            continue
        parts = _split_row(line)
        if not _is_data_row(parts, MIN_COLUMNS_ZVM):
            continue
        try:
            n_cpu = int(parts[1])
            u_c = _parse_float_hyptop(parts[2])
            u_m = _parse_float_hyptop(parts[3])
        except (ValueError, IndexError):
            LOG.debug("skip unparsable z/VM line: %r", line[:120])
            continue
        rows.append(
            LparRow(
                system=parts[0],
                num_cores=n_cpu,
                num_threads=n_cpu,
                core_percent=u_c,
                thread_percent=u_c,
                mgm_percent=u_m,
            )
        )
    return rows


def parse_hyptop_sys_list_text(
    text: str,
    hypervisor: HypervisorKind = "lpar",
) -> list[LparRow]:
    """
    Parse hyptop batch stdout for sys_list using the field set for `hypervisor`.
    """
    if hypervisor == "zvm":
        return _parse_zvm_rows(text)
    if hypervisor == "lpar":
        return _parse_lpar_rows(text)
    raise ValueError(f"unknown hypervisor: {hypervisor!r}")


def field_spec_for_hypervisor(hypervisor: HypervisorKind) -> str:
    if hypervisor == "zvm":
        return HYPTOP_FIELD_SPEC_ZVM
    if hypervisor == "lpar":
        return HYPTOP_FIELD_SPEC_LPAR
    raise ValueError(f"unknown hypervisor: {hypervisor!r}")


def run_hyptop_once(
    hyptop_bin: str = "hyptop",
    delay_seconds: int = 1,
    timeout_seconds: float = 30.0,
    hypervisor: HypervisorKind = "lpar",
) -> str:
    """
    Run hyptop in batch mode for a single screen update and return stdout (decoded).
    Does not pass -t; hyptop uses its default CPU-type mix.
    """
    field_spec = field_spec_for_hypervisor(hypervisor)
    args = [
        hyptop_bin,
        "-b",
        "-n",
        "1",
        "-d",
        str(delay_seconds),
        "-f",
        field_spec,
    ]
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or "(no stderr)"
        raise RuntimeError(f"hyptop exited {proc.returncode}: {err}")
    return proc.stdout
