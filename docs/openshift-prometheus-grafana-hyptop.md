# Collecting and viewing hyptop metrics with Prometheus and Grafana on OpenShift (without Cluster Observability Operator)

This guide explains how to run **Prometheus** and **Grafana** on OpenShift using **Red Hat container images**, scrape the [hyptop-dashboard](../README.md) exporter running on Linux in an LPAR or z/VM guest, and reach the UIs from outside the cluster via **Route**. It does **not** rely on **Cluster Observability Operator (COO)**.

| Component | Image (referenced in repo manifests) |
|-----------|----------------------------------------|
| Prometheus | `registry.redhat.io/openshift4/ose-prometheus-rhel9:v4.20` |
| Grafana | `registry.redhat.io/rhel10/grafana:10.1` |

---

## 1. Architecture and data flow

1. **Outside the cluster (or Linux on the same network)**: Install and run `hyptop-exporter` as in the [README](../README.md), listening on e.g. `0.0.0.0:9105`, exposing `http://<host-or-ip>:9105/metrics`.
2. **OpenShift namespace** (example `hyptop-observe`): Create a **Service** pointing at that address (often `ExternalName`; if resolution or scraping misbehaves, use a **ClusterIP Service + Endpoints** instead—see [Openshift/hyptopsrv.yaml](../Openshift/hyptopsrv.yaml) and below).
3. **Same namespace**: Deploy **Prometheus** (`StatefulSet` + **PVC** using the cluster’s **default StorageClass**), with `prometheus.yml` scraping `hyptopmetric.<namespace>.svc.cluster.local:9105` via `static_configs`.
4. **Grafana** (`Deployment` + **PVC**) uses the in-cluster Service `http://prometheus.<namespace>.svc:9090` as the default datasource; **Routes** expose HTTPS entry points for Prometheus and Grafana from outside the cluster.

Metric names and meanings are in the README [Metrics](../README.md#metrics) section.

---

## 2. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Image pulls | Nodes or the project must be able to pull from `registry.redhat.io` (subscription and pull secret). Typical approach: link a **pull secret** that includes `registry.redhat.io` to the namespace default `ServiceAccount`: `oc secrets link default <secret-name> --for=pull -n hyptop-observe`. |
| Default StorageClass | **PVCs omit `storageClassName`** so the cluster default StorageClass provisions volumes; if there is no default class, set `storageClassName` explicitly on PVCs / `volumeClaimTemplates`. |
| Networking | **Prometheus Pods** in the cluster must reach the exporter on **TCP 9105**. |
| Scrape interval | `scrape_interval` should be **no shorter than** the exporter’s `--interval-seconds` (README default 15s); the sample manifest uses 30s—align with your exporter as needed. |

---

## 3. Run the exporter on the LPAR / z/VM side

Same as the README, for example:

```bash
hyptop-exporter \
  --hypervisor lpar \
  --listen-host 0.0.0.0 \
  --listen-port 9105 \
  --interval-seconds 15 \
  --hyptop-binary /usr/sbin/hyptop
```

Verify reachability from the **network where Prometheus Pods will run**: `curl -sS http://<exporter-host>:9105/metrics | head`.

---

## 4. Declare the metrics endpoint (Service)

Repository example: [Openshift/hyptopsrv.yaml](../Openshift/hyptopsrv.yaml). Replace `<your namespace>` and `externalName` with real values; if you use this repo’s monitoring stack manifest, the namespace can match **`hyptop-observe`** as in [Openshift/prometheus-grafana-stack.yaml](../Openshift/prometheus-grafana-stack.yaml).

In some environments **ExternalName alone** does not resolve the scrape target as Prometheus expects; use **ClusterIP + Endpoints** instead (set subset addresses to a reachable exporter **IP**):

```yaml
apiVersion: v1
kind: Service
metadata:
  name: hyptopmetric
  namespace: hyptop-observe
  labels:
    app: hyptopmetric
spec:
  type: ClusterIP
  ports:
    - name: metrics
      port: 9105
      targetPort: 9105
      protocol: TCP
---
apiVersion: v1
kind: Endpoints
metadata:
  name: hyptopmetric
  namespace: hyptop-observe
  labels:
    app: hyptopmetric
subsets:
  - addresses:
      - ip: 10.0.0.50
    ports:
      - name: metrics
        port: 9105
        protocol: TCP
```

---

## 5. Deploy Prometheus and Grafana

1. **Change the Grafana admin password**: Edit `CHANGE_ME` in the `grafana-admin` `Secret` in `Openshift/prometheus-grafana-stack.yaml`, or delete that Secret and run:  
   `oc create secret generic grafana-admin --from-literal=password='<strong-password>' -n hyptop-observe`
2. **(Optional)** If the namespace is not `hyptop-observe`, replace `hyptop-observe` everywhere in the manifests and update the `static_configs` targets in `ConfigMap/prometheus-config` and the Prometheus URL in `grafana-datasources` accordingly.
3. **Apply the manifests**:

```bash
oc apply -f Openshift/hyptopsrv.yaml
oc apply -f Openshift/prometheus-grafana-stack.yaml
```

4. **Inspect Routes and Pods**:

```bash
oc get route,pod,pvc -n hyptop-observe
```

Prometheus and Grafana each have a **Route** (edge TLS termination); open the printed hosts with `https://` in a browser.

5. **Grafana login**: Username `admin`, password from the Secret above. Import [grafana/hyptop-lpar.json](../grafana/hyptop-lpar.json) (README [Grafana](../README.md#grafana)).

---

## 6. Persistence

| Resource | Notes |
|----------|-------|
| Prometheus | `volumeClaimTemplates` on the `StatefulSet` (volume name `prometheus-data`), sample size **20Gi**, no `storageClassName` set. |
| Grafana | `PersistentVolumeClaim` `grafana-data`, sample **5Gi**, no `storageClassName` set. |

Resize as needed; if volumes are already bound, follow your storage and operations practices to expand or rebuild.

---

## 7. External access and `externalUrl` (optional)

Route hostnames are assigned by the cluster or policy. If Prometheus redirects incorrectly or Grafana’s root URL is wrong, add `--web.external-url=https://<prometheus-route-host>/` to Prometheus and set `GF_SERVER_ROOT_URL=https://<grafana-route-host>/` for Grafana, then roll the workloads. Use the hosts from `oc get route -n hyptop-observe`.

---

## 8. Example queries

In the Prometheus UI (Route), for example:

```promql
hyptop_lpar_real_smt_utilization_hyptop_percent
```

Or:

```promql
hyptop_lpar_real_smt_utilization_hyptop_percent{system="YOUR_LPAR_NAME"}
```

Per-core normalized series (if your exporter version exposes them):

```promql
hyptop_lpar_core_per_num_cores_hyptop_percent
hyptop_lpar_real_smt_per_num_cores_hyptop_percent
```

---

## 9. Troubleshooting

| Symptom | Suggestion |
|---------|------------|
| Grafana Pod fails, `ReplicaFailure` / SCC errors mentioning `fsGroup` / `472` | Default **`restricted-v2`** only allows namespace-scoped supplemental groups—**do not** hard-code `securityContext.fsGroup: 472` on the Pod (common upstream Grafana samples). This repo’s manifests omit `fsGroup` so admission/SCC assigns compliant UID and groups. If `/var/lib/grafana` still cannot be written, check `oc describe pod` and container logs; coordinate with cluster admins for a dedicated SCC or storage permissions—**do not** default to `privileged` in documented paths. |
| ImagePullBackOff | Verify `registry.redhat.io` pull secret linkage to `ServiceAccount`; confirm subscription and image tags exist. |
| PVC Pending | Confirm a default StorageClass or set `storageClassName` explicitly. |
| Target DOWN / no metrics | From a Prometheus Pod, test connectivity to exporter:9105; check firewalls, `hyptopsrv` Service/Endpoints, and that `prometheus.yml` target FQDN matches the namespace. |
| `hyptop_exporter_collection_success` is 0 | Issue is on the exporter or local hyptop—see README [Troubleshooting](../README.md#troubleshooting). |

---

## 10. Comparison with COO / user-workload monitoring (brief)

- **This approach**: User-namespace Prometheus/Grafana with a normal `prometheus.yml` scrape config—**no** `MonitoringStack` / `monitoring.rhobs` CRDs.
- **Cluster Observability Operator**: Operator-managed MonitoringStack and related resources (historical note: this repo favors the self-managed stack).
- **CMO user-workload monitoring**: Uses `monitoring.coreos.com` `ServiceMonitor` etc., scraped by the cluster’s user-workload Prometheus—a different path from “self-managed Prometheus Deployment/StatefulSet” in these manifests.

---

## 11. References

- [hyptop-dashboard README](../README.md)
- [Openshift/prometheus-grafana-stack.yaml](../Openshift/prometheus-grafana-stack.yaml)
- [Openshift/hyptopsrv.yaml](../Openshift/hyptopsrv.yaml)
- IBM hyptop: [hyptop command](https://www.ibm.com/docs/en/linux-on-systems?topic=commands-hyptop)
