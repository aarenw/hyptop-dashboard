# hyptop-dashboard

在 IBM Z 和 LinuxONE 上，计算资源常被过度超配。本仓库提供一个在 **LPAR 的 Linux** 上运行的小型 **Prometheus exporter**：周期性调用 [hyptop](https://www.ibm.com/docs/en/linux-on-systems?topic=c-hyptop) 采集 IFL 相关指标，按 [SMT 真实利用率](https://linux.mainframe.blog/smt_utilization/) 公式计算 **SMT 调整后的利用率**，并通过 HTTP `/metrics` 供 Prometheus 抓取；可用 Grafana 展示 LPAR 利用率与其它导出的指标。

On IBM Z and LinuxONE, compute capacity is often oversubscribed. This repo ships a **Prometheus exporter** that runs on **Linux in the LPAR**, periodically runs **hyptop**, computes **SMT-adjusted “real” utilization** (see formula below), and exposes **Prometheus** metrics over HTTP for **Grafana** dashboards.

## Requirements

- Linux on LPAR with `hyptop` installed and permission to read the relevant **debugfs** hyptop data (see IBM hyptop documentation).
- Python **3.9+** and `prometheus-client`.
- Network path from **Prometheus** to the LPAR (or collector host) on the exporter listen port.

## SMT-adjusted utilization

Using hyptop’s **core** (`u_c`), **thread** (`u_t`), and **management** (`u_m`) values in hyptop’s **%** display (same scale as IBM examples; **100 ≈ one IFL** for that component), and a configurable SMT speedup **s** (default **1.3**, a z15 rule of thumb from the blog):


u_r = \frac{2u_c - u_t}{s} + (u_t - u_c) + u_m


Reference: [SMT: What utilization in real?](https://linux.mainframe.blog/smt_utilization/)

## Install

From the repository root (use a venv on the LPAR):

```bash
mkdir /opt/hyptop-dashboard
cd /opt/hyptop-dashboard/
git clone git@github.com:aarenw/hyptop-dashboard.git .
python3 -m venv /opt/hyptop-dashboard/.venv
/opt/hyptop-dashboard/.venv/bin/pip install --upgrade pip
/opt/hyptop-dashboard/.venv/bin/pip install .
```

If `pip` copies the tree to a temp dir and fails on `.git`, use in-tree build:

```bash
/opt/hyptop-dashboard/.venv/bin/pip install --use-feature=in-tree-build .
```

Alternatively, without installing the package:

```bash
export PYTHONPATH=/path/to/hyptop-dashboard/src
python3 -m hyptop_dashboard --help
```

## Run the exporter

The exporter runs `hyptop` in batch mode with LPAR fields `#,T,c,e,m,C,E,M,o` (IBM example: `hyptop -f "#,T,c,e,m,C,E,M,o"`), then parses **sys_list** rows.

```bash
hyptop-exporter \
  --listen-host 0.0.0.0 \
  --listen-port 9105 \
  --interval-seconds 15 \
  --hyptop-binary /usr/bin/hyptop \
  --hyptop-delay 1 \
  --hyptop-cpu-types ifl \
  --smt-speedup 1.3
```


| Flag                              | Meaning                                      |
| --------------------------------- | -------------------------------------------- |
| `--listen-host` / `--listen-port` | HTTP bind address for `/metrics`             |
| `--interval-seconds`              | Sleep between `hyptop` runs                  |
| `--hyptop-binary`                 | Path to `hyptop`                             |
| `--hyptop-delay`                  | Passed to `hyptop -d`                        |
| `--hyptop-cpu-types`              | Passed to `hyptop -t` (e.g. `ifl`, `ifl,cp`) |
| `--omit-hyptop-cpu-types`         | Do not pass `hyptop -t`                      |
| `--hyptop-timeout`                | Subprocess timeout per `hyptop` invocation   |
| `--smt-speedup`                   | **s** in the formula above                   |
| `-v`                              | Verbose logging                              |


**Scrape interval:** Set Prometheus `scrape_interval` to **at least** `--interval-seconds` (or accept that samples may repeat between hyptop runs).

## systemd

Example unit: [deploy/hyptop-exporter.service](deploy/hyptop-exporter.service). Adjust `User=`, paths, and flags for your site.

```bash
sudo cp deploy/hyptop-exporter.service /etc/systemd/system/hyptop-exporter.service
sudo systemctl daemon-reload
sudo systemctl enable --now hyptop-exporter
sudo systemctl status hyptop-exporter
```

**Firewall:** Allow Prometheus (or your scrapers) to reach `LISTEN_PORT` (default **9105**) on the LPAR.

## Prometheus

```yaml
scrape_configs:
  - job_name: hyptop
    scrape_interval: 15s
    static_configs:
      - targets: ["lpar-host.example.com:9105"]
```

## Grafana

Import [grafana/hyptop-lpar.json](grafana/hyptop-lpar.json): **Dashboards → New → Import → Upload JSON**. Choose your Prometheus datasource when prompted.

## Metrics


| Metric                                              | Labels   | Description                                  |
| --------------------------------------------------- | -------- | -------------------------------------------- |
| `hyptop_lpar_core_utilization_hyptop_percent`       | `system` | Core dispatch (hyptop % units)               |
| `hyptop_lpar_thread_utilization_hyptop_percent`     | `system` | Thread time (hyptop % units)                 |
| `hyptop_lpar_management_utilization_hyptop_percent` | `system` | Management time (hyptop % units)             |
| `hyptop_lpar_real_smt_utilization_hyptop_percent`   | `system` | SMT-adjusted utilization (same units)        |
| `hyptop_lpar_num_cores`                             | `system` | `#core` from hyptop                          |
| `hyptop_lpar_num_threads`                           | `system` | `#The` from hyptop                           |
| `hyptop_exporter_collection_success`                | —        | `1` if last `hyptop` run and parse succeeded |
| `hyptop_exporter_last_success_timestamp_seconds`    | —        | Unix time of last successful collection      |


Time series for LPARs that disappear from hyptop output are removed on the next successful scrape.

## Hyptop references

- [hyptop command](https://www.ibm.com/docs/en/linux-on-systems?topic=commands-hyptop)
- [LPAR fields](https://www.ibm.com/docs/en/linux-on-systems?topic=fu-lpar-fields)
- [Units](https://www.ibm.com/docs/en/linux-on-systems?topic=fu-units)
- [CPU types](https://www.ibm.com/docs/en/linux-on-systems?topic=h-cpu-types)
- [Examples](https://www.ibm.com/docs/en/linux-on-systems?topic=h-examples)

## Troubleshooting

- `**hyptop_exporter_collection_success` is 0:** Check journal/logs for `hyptop` errors, permissions on `/s390_hypfs`, and HMC “Global performance data” for other LPARs if needed.
- **No series / empty `system` list:** Confirm batch output includes the `#,T,c,e,m,...` columns; run `hyptop -b -n 1 -f "#,T,c,e,m,C,E,M,o"` manually.
- **Values look wrong:** Tune `--hyptop-cpu-types` and `--smt-speedup` (**s**) for your hardware and workload; the blog notes **s** is workload-dependent.

## Development

```bash
pip install -e ".[dev]"   # or: pip install pytest && PYTHONPATH=src pytest
pytest
```

## 原始需求对照

1. 脚本：周期性运行 hyptop，计算利用率，通过 Prometheus 抓取 HTTP 指标（`hyptop-exporter` / `python -m hyptop_dashboard`）。
2. systemd：`deploy/hyptop-exporter.service` 与本文安装说明。
3. Grafana：`grafana/hyptop-lpar.json`。
4. README：本文档（原「晚上更新 README」按「完善 README」理解，已合并为正式说明）。

