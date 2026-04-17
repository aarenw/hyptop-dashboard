# Collecting and visualizing hyptop metrics with the OpenShift Monitoring Stack

This guide uses the OpenShift **user-workload-monitoring** stack to scrape metrics from `hyptop-exporter`, and adds a dashboard to **Observe -> Dashboards** in the OpenShift Console.

It is an alternative to the self-managed stack documented in [openshift-prometheus-grafana-hyptop.md](openshift-prometheus-grafana-hyptop.md).

## 1. Architecture

1. `hyptop-exporter` runs outside (or reachable from) the cluster and exposes `http://<host>:9105/metrics`.
2. A Service in namespace `hyptop-observe` maps that endpoint (`ExternalName` or `ClusterIP + Endpoints`).
3. A `ServiceMonitor` lets user-workload Prometheus scrape it.
4. A `PrometheusRule` defines basic exporter alerts.
5. A dashboard ConfigMap in `openshift-config-managed` makes a Grafana-style dashboard visible in OpenShift Console.

## 2. Prerequisites

- OpenShift cluster with cluster-admin access for monitoring configuration.
- `hyptop-exporter` already running and reachable on TCP `9105`.
- Network path from OpenShift pods to exporter host.
- `oc` CLI logged in as a user with required permissions.

## 3. Enable (or verify) user-workload-monitoring

Enable once per cluster:

```bash
oc -n openshift-monitoring get configmap cluster-monitoring-config -o yaml
```

If it is missing or does not include `enableUserWorkload: true`, apply:

```bash
cat <<'EOF' | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-monitoring-config
  namespace: openshift-monitoring
data:
  config.yaml: |
    enableUserWorkload: true
EOF
```

Wait for user-workload monitoring pods:

```bash
oc get pods -n openshift-user-workload-monitoring
```

## 4. Deploy hyptop monitoring resources

1. Create or update the exporter Service:

```bash
oc apply -f Openshift/hyptopsrv.yaml
```

2. Edit [`Openshift/hyptopsrv.yaml`](../Openshift/hyptopsrv.yaml) placeholders first:
   - `namespace: <your namespace>`
   - `externalName: <hyptop metric server DNS name>`

3. Apply OCP monitoring resources:

```bash
oc apply -f Openshift/ocp-monitoring-stack.yaml
```

If your namespace is not `hyptop-observe`, update it in both YAML files before applying.

## 5. Deploy the OpenShift Console dashboard

```bash
oc apply -f Openshift/ocp-console-dashboard.yaml
```

This creates a ConfigMap in `openshift-config-managed` with `console.openshift.io/dashboard=true`.

## 6. Validate metric ingestion

Check resources:

```bash
oc get servicemonitor,prometheusrule -n hyptop-observe
oc get svc -n hyptop-observe hyptopmetric
```

In **Observe -> Metrics**, run:

```promql
hyptop_lpar_real_smt_utilization_hyptop_percent
```

Or filter one system:

```promql
hyptop_lpar_real_smt_utilization_hyptop_percent{system="YOUR_LPAR_NAME"}
```

Health checks:

```promql
up{job="hyptop-exporter"}
hyptop_exporter_collection_success
```

## 7. Open dashboard in OpenShift Console

1. Open **Observe -> Dashboards**.
2. Select dashboard **Hyptop LPAR utilization**.
3. Use variable **LPAR / system** (`$lpar`, default `.*` for “All”) to filter by the `system` label.

## 8. Alert rules included

[`Openshift/ocp-monitoring-stack.yaml`](../Openshift/ocp-monitoring-stack.yaml) adds:

- `HyptopExporterTargetDown`: scrape target is down for 5 minutes.
- `HyptopExporterCollectionFailing`: no successful exporter collection for 10 minutes.

## 9. Troubleshooting

- **No metrics in queries**
  - Confirm exporter endpoint from cluster network.
  - Check Service target and DNS resolution (`ExternalName`) or switch to `ClusterIP + Endpoints`.
  - Verify `ServiceMonitor` exists in the same namespace and matches label `app: hyptopmetric`.

- **Dashboard does not appear**
  - Check ConfigMap exists in `openshift-config-managed`.
  - Verify labels:
    - `console.openshift.io/dashboard=true`
    - optional `console.openshift.io/odc-dashboard=true`
  - Refresh console or re-login.

- **Alerts never fire**
  - Confirm data exists for `up{job="hyptop-exporter"}` and `hyptop_exporter_collection_success`.
  - Confirm rule object in `hyptop-observe` and no parse errors in rule status/events.

## 10. Choosing between deployment models

- Use this guide when you want **native OCP monitoring integration**, centralized alerting, and OpenShift Console dashboards.
- Use [openshift-prometheus-grafana-hyptop.md](openshift-prometheus-grafana-hyptop.md) when you need a **fully self-managed** Prometheus + Grafana stack in your own namespace.
