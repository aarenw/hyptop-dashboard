"""Parse hyptop batch output (LPAR sys_list with thread fields)."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

LOG = logging.getLogger(__name__)

# LPAR sys_list fields: #core, #The, core%, the%, mgm%, then cumulative time columns
HYPTOP_FIELD_SPEC = "#,T,c,e,m,C,E,M,o"

# Minimum columns: system, #core, #The, core, the, mgm, ...
MIN_COLUMNS = 6


@dataclass(frozen=True)
class LparRow:
    """One LPAR line from hyptop sys_list."""

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


def _is_data_row(parts: list[str]) -> bool:
    if len(parts) < MIN_COLUMNS:
        return False
    system = parts[0]
    if not system or system == "(str)":
        return False
    if re.match(r"^\d{2}:\d{2}:\d{2}", system):
        return False
    if system == "system":
        return False
    # Aggregate/footer rows often start with a numeric-only "name"
    if system.isdigit():
        return False
    return True


def parse_hyptop_sys_list_text(text: str) -> list[LparRow]:
    """
    Parse stdout of: hyptop -b -n 1 ... -f "#,T,c,e,m,C,E,M,o"
    """
    rows: list[LparRow] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith("?=help"):
            continue
        parts = _split_row(line)
        if not _is_data_row(parts):
            continue
        try:
            n_core = int(parts[1])
            n_the = int(parts[2])
            u_c = float(parts[3])
            u_t = float(parts[4])
            u_m = float(parts[5])
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


def run_hyptop_once(
    hyptop_bin: str = "hyptop",
    delay_seconds: int = 1,
    timeout_seconds: float = 30.0,
) -> str:
    """
    Run hyptop in batch mode for a single screen update and return stdout (decoded).
    Does not pass -t; hyptop uses its default CPU-type mix.
    """
    args = [
        hyptop_bin,
        "-b",
        "-n",
        "1",
        "-d",
        str(delay_seconds),
        "-f",
        HYPTOP_FIELD_SPEC,
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
