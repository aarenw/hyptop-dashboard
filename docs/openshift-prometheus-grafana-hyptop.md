# 在 OpenShift 上用 Prometheus 与 Grafana 采集、查看 hyptop 指标（不使用 Cluster Observability Operator）

本文说明如何在 OpenShift 上使用 **红帽容器镜像** 自建 **Prometheus** 与 **Grafana**，抓取运行在 LPAR / z/VM Linux 上的 [hyptop-dashboard](../README.md) exporter，并通过 **Route** 从集群外访问 UI。不再依赖 **Cluster Observability Operator（COO）**。

| 组件 | 镜像（仓库内清单已引用） |
|------|-------------------------|
| Prometheus | `registry.redhat.io/openshift4/ose-prometheus-rhel9:v4.20` |
| Grafana | `registry.redhat.io/rhel10/grafana:10.1` |

---

## 1. 架构与数据流

1. **集群外（或同网段 Linux）**：按 [README 安装与运行](../README.md) `hyptop-exporter`，监听例如 `0.0.0.0:9105`，对外提供 `http://<主机或 IP>:9105/metrics`。
2. **OpenShift 命名空间**（示例 `hyptop-observe`）：创建指向该地址的 **Service**（常用 `ExternalName`；若发现解析/抓取异常，改用 **ClusterIP Service + Endpoints**，见 [Openshift/hyptopsrv.yaml](../Openshift/hyptopsrv.yaml) 与下文）。
3. **同一命名空间**：部署 **Prometheus**（`StatefulSet` + **PVC**，使用集群**默认 StorageClass**），`prometheus.yml` 中通过 `static_configs` 抓取 `hyptopmetric.<namespace>.svc.cluster.local:9105`。
4. **Grafana**（`Deployment` + **PVC**）通过集群内 Service `http://prometheus.<namespace>.svc:9090` 作为默认数据源；**Route** 暴露 Prometheus 与 Grafana 的 HTTPS 入口供集群外访问。

指标名称与含义见 README 中的 [Metrics](../README.md#metrics)。

---

## 2. 前提条件

| 条件 | 说明 |
|------|------|
| 拉取镜像 | 节点或项目需能拉取 `registry.redhat.io`（订阅与 pull secret）。常用做法：将含 `registry.redhat.io` 的 **pull secret** 关联到命名空间默认 `ServiceAccount`：`oc secrets link default <secret-name> --for=pull -n hyptop-observe`。 |
| 默认 StorageClass | **PVC 不填写 `storageClassName`**，由集群默认 StorageClass 绑定卷；若无默认类，需自行在 PVC / `volumeClaimTemplates` 中指定。 |
| 网络 | 运行在集群内的 **Prometheus Pod** 必须能访问 exporter 的 **TCP 9105**。 |
| 抓取间隔 | `scrape_interval` 建议 **不小于** exporter 的 `--interval-seconds`（README 默认 15s）；清单示例为 30s，可按需与 exporter 对齐。 |

---

## 3. LPAR / z/VM 侧运行 exporter

与 README 一致，例如：

```bash
hyptop-exporter \
  --hypervisor lpar \
  --listen-host 0.0.0.0 \
  --listen-port 9105 \
  --interval-seconds 15 \
  --hyptop-binary /usr/sbin/hyptop
```

确认从 **将来运行 Prometheus 的 Pod 所在网段** 可访问：`curl -sS http://<exporter-host>:9105/metrics | head`。

---

## 4. 声明指标端点（Service）

仓库示例：[Openshift/hyptopsrv.yaml](../Openshift/hyptopsrv.yaml)。将 `<your namespace>` 与 `externalName` 换成实际值；若使用本仓库的监控栈清单，命名空间可与 [Openshift/prometheus-grafana-stack.yaml](../Openshift/prometheus-grafana-stack.yaml) 一致为 **`hyptop-observe`**。

部分环境下 **仅 ExternalName** 时 Prometheus 对 target 的解析行为不符合预期，可改用 **ClusterIP + Endpoints**（把子集地址设为 exporter 的可达 **IP**）：

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

## 5. 部署 Prometheus 与 Grafana

1. **修改 Grafana 管理员密码**：编辑 `Openshift/prometheus-grafana-stack.yaml` 中 `Secret` `grafana-admin` 的 `CHANGE_ME`，或删除该 Secret 资源后执行：  
   `oc create secret generic grafana-admin --from-literal=password='<strong-password>' -n hyptop-observe`
2. **（可选）** 若命名空间不是 `hyptop-observe`，请全局替换清单中的 `hyptop-observe`，并同步修改 `ConfigMap/prometheus-config` 里 `static_configs` 的 target 与 `grafana-datasources` 中的 Prometheus URL。
3. **应用清单**：

```bash
oc apply -f Openshift/hyptopsrv.yaml
oc apply -f Openshift/prometheus-grafana-stack.yaml
```

4. **查看 Route 与 Pod**：

```bash
oc get route,pod,pvc -n hyptop-observe
```

Prometheus 与 Grafana 各有一条 **Route**（边缘 TLS 终止），浏览器使用 `https://` 打开控制台给出的 host。

5. **Grafana 登录**：用户名为 `admin`，密码为上述 Secret 中的值。导入仓库中的 [grafana/hyptop-lpar.json](../grafana/hyptop-lpar.json)（README [Grafana](../README.md#grafana)）。

---

## 6. 持久化说明

| 资源 | 说明 |
|------|------|
| Prometheus | `StatefulSet` 的 `volumeClaimTemplates`（卷名 `prometheus-data`），示例请求 **20Gi**，未设置 `storageClassName`。 |
| Grafana | `PersistentVolumeClaim` `grafana-data`，示例 **5Gi**，未设置 `storageClassName`。 |

按需调整容量；修改后若已绑定 PV，请遵循存储与运维规范扩容或重建。

---

## 7. 集群外访问与 `externalUrl`（可选）

Route 的 hostname 由集群分配或策略决定。若 Prometheus 重定向或 Grafana 根 URL 异常，可在 Prometheus 上增加参数 `--web.external-url=https://<prometheus-route-host>/`，在 Grafana 环境变量中设置 `GF_SERVER_ROOT_URL=https://<grafana-route-host>/`，然后滚动重启对应工作负载。具体 host 以 `oc get route -n hyptop-observe` 为准。

---

## 8. 查询示例

在 Prometheus UI（Route）中执行，例如：

```promql
hyptop_lpar_real_smt_utilization_hyptop_percent
```

或：

```promql
hyptop_lpar_real_smt_utilization_hyptop_percent{system="YOUR_LPAR_NAME"}
```

---

## 9. 故障排查

| 现象 | 建议 |
|------|------|
| Grafana Pod 无法创建、`ReplicaFailure` / SCC 报错含 `fsGroup` / `472` | 默认 **`restricted-v2`** 只允许命名空间范围内的 supplemental group，**不要**在 Pod 上写死 `securityContext.fsGroup: 472`（常见上游 Grafana 示例）。本仓库清单已省略 `fsGroup`，由准入/SCC 分配合规的 UID 与组。若仍无法写入 `/var/lib/grafana`，查看 `oc describe pod` 与容器日志；需放宽策略时与集群管理员确认（例如专用 SCC 或存储侧权限），勿在文档化路径中默认使用 `privileged`。 |
| ImagePullBackOff | 检查 `registry.redhat.io` pull secret 与 `ServiceAccount` 关联；确认订阅与镜像标签存在。 |
| PVC Pending | 确认默认 StorageClass 或改为显式 `storageClassName`。 |
| Target DOWN / 无指标 | 从 Prometheus Pod 测试到 exporter:9105；检查防火墙、`hyptopsrv` 的 Service/Endpoints、以及 `prometheus.yml` 中 target FQDN 是否与命名空间一致。 |
| `hyptop_exporter_collection_success` 为 0 | 问题在 exporter 或本机 hyptop，见 README [Troubleshooting](../README.md#troubleshooting)。 |

---

## 10. 与 COO / 用户工作负载监控的对比（简要）

- **本方案**：用户命名空间内自建 Prometheus/Grafana，抓取配置为普通 `prometheus.yml`，**无** `MonitoringStack` / `monitoring.rhobs` CRD。
- **Cluster Observability Operator**：由 Operator 管理 MonitoringStack 等，见历史说明（本仓库已改为以自建栈为主）。
- **CMO 用户工作负载监控**：使用 `monitoring.coreos.com` 的 `ServiceMonitor` 等，由集群用户负载 Prometheus 抓取；与本清单的“自建 Prometheus Deployment/StatefulSet”是不同路径。

---

## 11. 参考链接

- [hyptop-dashboard README](../README.md)
- [Openshift/prometheus-grafana-stack.yaml](../Openshift/prometheus-grafana-stack.yaml)
- [Openshift/hyptopsrv.yaml](../Openshift/hyptopsrv.yaml)
- IBM hyptop：[hyptop command](https://www.ibm.com/docs/en/linux-on-systems?topic=commands-hyptop)
