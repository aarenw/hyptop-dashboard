"""CLI: periodic hyptop collection and Prometheus HTTP /metrics."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from typing import cast

from prometheus_client import Gauge, start_http_server

from hyptop_dashboard.parser import HypervisorKind, parse_hyptop_sys_list_text, real_smt_utilization_percent, run_hyptop_once

LOG = logging.getLogger(__name__)

# HELP: values use hyptop's %% display: 100 == one IFL worth of dispatch for that component.
CORE_G = Gauge(
    "hyptop_lpar_core_utilization_hyptop_percent",
    "Core (LPAR) or CPU (z/VM) time per second in hyptop percent units (100 = 1 IFL). "
    "Label system is LPAR name or z/VM guest id.",
    ["system"],
)
THREAD_G = Gauge(
    "hyptop_lpar_thread_utilization_hyptop_percent",
    "Thread time per second (LPAR #The/e); on z/VM equals core cpu% (no separate thread field).",
    ["system"],
)
MGM_G = Gauge(
    "hyptop_lpar_management_utilization_hyptop_percent",
    "Management time per second in hyptop percent units (100 = 1 IFL).",
    ["system"],
)
REAL_G = Gauge(
    "hyptop_lpar_real_smt_utilization_hyptop_percent",
    "SMT-adjusted utilization in hyptop percent units; on z/VM degenerates to u_c/s+u_m (u_t=u_c). "
    "See linux.mainframe.blog/smt_utilization/.",
    ["system"],
)
CORE_PER_NUM_CORES_G = Gauge(
    "hyptop_lpar_core_per_num_cores_hyptop_percent",
    "hyptop_lpar_core_utilization_hyptop_percent divided by hyptop_lpar_num_cores (hyptop percent scale per configured core).",
    ["system"],
)
REAL_PER_NUM_CORES_G = Gauge(
    "hyptop_lpar_real_smt_per_num_cores_hyptop_percent",
    "hyptop_lpar_real_smt_utilization_hyptop_percent divided by hyptop_lpar_num_cores "
    "(SMT-adjusted hyptop percent scale per configured core).",
    ["system"],
)
NUM_CORE_G = Gauge(
    "hyptop_lpar_num_cores",
    "LPAR #core or z/VM #cpu from hyptop sys_list.",
    ["system"],
)
NUM_THREAD_G = Gauge(
    "hyptop_lpar_num_threads",
    "LPAR #The; z/VM sets equal to #cpu (no thread column in this field set).",
    ["system"],
)
SCRAPE_OK = Gauge(
    "hyptop_exporter_collection_success",
    "1 if the last hyptop run and parse succeeded, else 0.",
)
LAST_OK_TS = Gauge(
    "hyptop_exporter_last_success_timestamp_seconds",
    "Unix time of last successful collection.",
)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _update_metrics(rows, s: float) -> set[str]:
    seen: set[str] = set()
    for row in rows:
        seen.add(row.system)
        u_r = real_smt_utilization_percent(
            row.core_percent,
            row.thread_percent,
            row.mgm_percent,
            s,
        )
        CORE_G.labels(row.system).set(row.core_percent)
        THREAD_G.labels(row.system).set(row.thread_percent)
        MGM_G.labels(row.system).set(row.mgm_percent)
        REAL_G.labels(row.system).set(u_r)
        NUM_CORE_G.labels(row.system).set(row.num_cores)
        NUM_THREAD_G.labels(row.system).set(row.num_threads)
        if row.num_cores > 0:
            inv = 1.0 / row.num_cores
            CORE_PER_NUM_CORES_G.labels(row.system).set(row.core_percent * inv)
            REAL_PER_NUM_CORES_G.labels(row.system).set(u_r * inv)
        else:
            LOG.debug("zero num_cores for system %s; per-core gauges set to 0", row.system)
            CORE_PER_NUM_CORES_G.labels(row.system).set(0.0)
            REAL_PER_NUM_CORES_G.labels(row.system).set(0.0)
    return seen


def _remove_stale_labels(
    previous: set[str],
    current: set[str],
    gauges: list[Gauge],
) -> None:
    """Drop time series for LPARs that disappeared from hyptop output."""
    stale = previous - current
    for name in stale:
        for g in gauges:
            try:
                g.remove(name)
            except KeyError:
                pass


def _collection_loop(
    *,
    interval: float,
    hyptop_bin: str,
    hyptop_delay: int,
    hyptop_timeout: float,
    s: float,
    hypervisor: HypervisorKind,
    gauges: list[Gauge],
) -> None:
    previous_systems: set[str] = set()
    while True:
        try:
            out = run_hyptop_once(
                hyptop_bin=hyptop_bin,
                delay_seconds=hyptop_delay,
                timeout_seconds=hyptop_timeout,
                hypervisor=hypervisor,
            )
            rows = parse_hyptop_sys_list_text(out, hypervisor=hypervisor)
            if not rows:
                LOG.warning(
                    "hyptop produced no sys_list rows (%s); check -f fields and permissions",
                    hypervisor,
                )
            current = _update_metrics(rows, s)
            _remove_stale_labels(previous_systems, current, gauges)
            previous_systems = current
            SCRAPE_OK.set(1)
            LAST_OK_TS.set(time.time())
        except Exception:
            LOG.exception("collection failed")
            SCRAPE_OK.set(0)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hyptop LPAR metrics Prometheus exporter.")
    p.add_argument(
        "--listen-host",
        default="0.0.0.0",
        help="Address for /metrics HTTP server (default 0.0.0.0).",
    )
    p.add_argument(
        "--listen-port",
        type=int,
        default=9105,
        help="Port for /metrics (default 9105).",
    )
    p.add_argument(
        "--interval-seconds",
        type=float,
        default=15.0,
        help="Sleep between hyptop runs (default 15).",
    )
    p.add_argument(
        "--hyptop-binary",
        default="hyptop",
        help="Path to hyptop executable (default hyptop).",
    )
    p.add_argument(
        "--hyptop-delay",
        type=int,
        default=1,
        help="hyptop -d delay between screen updates in batch (default 1).",
    )
    p.add_argument(
        "--hypervisor",
        choices=("lpar", "zvm"),
        default="lpar",
        help="sys_list field set: lpar uses -f '#,T,c,e,m,C,E,M,o'; zvm uses -f '#,c,m,C,M,o'.",
    )
    p.add_argument(
        "--hyptop-timeout",
        type=float,
        default=30.0,
        help="Subprocess timeout seconds for each hyptop run (default 30).",
    )
    p.add_argument(
        "--smt-speedup",
        type=float,
        default=1.3,
        help="SMT speedup factor s in real utilization formula (default 1.3, z15 rule of thumb).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    _configure_logging(args.verbose)

    if args.smt_speedup <= 0:
        LOG.error("--smt-speedup must be positive")
        return 2

    gauges = [
        CORE_G,
        THREAD_G,
        MGM_G,
        REAL_G,
        CORE_PER_NUM_CORES_G,
        REAL_PER_NUM_CORES_G,
        NUM_CORE_G,
        NUM_THREAD_G,
    ]

    start_http_server(args.listen_port, addr=args.listen_host)
    LOG.info(
        "listening on http://%s:%s/metrics; collecting every %ss",
        args.listen_host,
        args.listen_port,
        args.interval_seconds,
    )

    thread = threading.Thread(
        target=_collection_loop,
        kwargs={
            "interval": args.interval_seconds,
            "hyptop_bin": args.hyptop_binary,
            "hyptop_delay": args.hyptop_delay,
            "hyptop_timeout": args.hyptop_timeout,
            "s": args.smt_speedup,
            "hypervisor": cast(HypervisorKind, args.hypervisor),
            "gauges": gauges,
        },
        daemon=True,
    )
    thread.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        LOG.info("exiting")
        return 0


if __name__ == "__main__":
    sys.exit(main())
