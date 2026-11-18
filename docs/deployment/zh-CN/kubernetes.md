# Kubernetes 开发与维护补充

[部署入口](../README.md) · 简体中文 | [English](../en/kubernetes.md)

已有集群上的环境要求、部署、验收与 HTTPS 配置统一维护在 [Kubernetes 部署文档](https://aivision.mintlify.site/zh-CN/deployment/kubernetes)。本页补充 Linux 本地 kind 环境及实现边界；参数与运维命令见 [aiv CLI](../../../aivision-cli/README.md#commands)。

固定产品资源的权威源是 [deploy/kubernetes/base/](../../../deploy/kubernetes/base/)，上游依赖和许可证在 [dependencies/](../../../deploy/kubernetes/dependencies/)。CLI 内嵌目录是 `make assets` 生成的兼容副本。产品安装继续使用 `aiv cast --target kubernetes`；`aiv generate --target kubernetes` 是已弃用的通用材料转换，缺少固定产品路径的初始化能力，不能替代安装命令。

Helm 部署已确定为后续方向，当前不可用，本次不实现或发布 Chart，也不提供可执行 Helm 安装/升级命令。[Helm roadmap](../../../deploy/kubernetes/helm-roadmap.md) 记录当前配置到未来 values 的映射，以及初始化、升级和持久资源所有权边界；它不是可执行 values 契约。现有 CLI 部署继续保留原 Secret、PVC 和身份，不自动转交 Helm 管理。

角色命名采用[统一映射](../../../deploy/README.md#组件命名与兼容)。CLI 的新旧选择器均可用；新增 `app.kubernetes.io/component` 标签，保留已有 workload/Service/Job 名称、旧 selectors、DNS、Secrets、namespace/state 和 PVC。目录归拢不会扩大支持的数据库组合。新角色接口需要包含此次变化的 CLI，不能从旧发布包版本号推断。

静态 CI 检查资源渲染、标准资源和固定 CRD 的 schema；模拟集群测试检查生命周期契约。这些结果与真实 Kubernetes 部署分开报告。实际升级后检查组织身份、凭证、PVC 绑定及该目标支持的遥测读写，不把 Docker 验收当作集群验收。

配套源码生命周期要求镜像支持后端 `/api/v1/ready`、诊断 `/internal/readyz`、有界停机和网关 `/app/collector healthcheck --ready`。默认原生 `healthcheck` 只做 2 秒本地存活检查；readiness 三段各 2 秒，Kubernetes 留 10 秒预算。源码合入 main 不代表具备这些能力的镜像已发布。完整[资源与生命周期契约](../../../deploy/kubernetes/lifecycle.md)说明单副本 Recreate、按 requests 至少剩余 3.4 CPU / 14Gi 的预检、每应用独立的 `TIMEOUT=30m` 和终止预算。4 CPU / 16Gi 是评估环境调度起点，不是经过负载验证的生产容量；readiness 或静态校验不能替代遥测、负载和镜像升级验收。

## Linux 本地 kind 环境

仅在需要创建本地测试集群时执行。准备运行中的 Docker、`curl`、`tar`、`sha256sum`、`kubectl` 与[已安装的 aiv CLI](https://aivision.mintlify.site/zh-CN/guides/install-cli)。已有集群直接按文档站部署，不运行下面的创建操作。

安装 kind（已安装则跳过）：

```bash
export PATH="$HOME/.local/bin:$PATH"; command -v kind >/dev/null || (set -eu; case "$(uname -m)" in x86_64|amd64) AIV_KIND_ARCH=amd64 ;; aarch64|arm64) AIV_KIND_ARCH=arm64 ;; *) echo 'Unsupported architecture' >&2; exit 1 ;; esac; AIV_KIND_TMP=$(mktemp -d); trap 'rm -rf -- "$AIV_KIND_TMP"' EXIT; cd "$AIV_KIND_TMP"; AIV_KIND_URL="https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-$AIV_KIND_ARCH"; curl -fLO "$AIV_KIND_URL"; curl -fLO "$AIV_KIND_URL.sha256sum"; sha256sum -c "kind-linux-$AIV_KIND_ARCH.sha256sum"; mkdir -p "$HOME/.local/bin"; install -m 755 "kind-linux-$AIV_KIND_ARCH" "$HOME/.local/bin/kind")
```

设置数据目录，按需修改路径：

```bash
AIV_K8S_DATA="$HOME/aiv-k8s-data"
```

创建 `aivision` 集群：

```bash
(set -eu; case "$AIV_K8S_DATA" in *$'\n'*|*$'\r'*) echo 'Invalid data directory' >&2; exit 1 ;; esac; mkdir -p -- "$AIV_K8S_DATA"; AIV_K8S_DATA=$(cd -- "$AIV_K8S_DATA" && pwd -P); AIV_CGROUP=$(stat -fc %T /sys/fs/cgroup); printf "kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nnetworking:\n  apiServerAddress: 127.0.0.1\nnodes:\n- role: control-plane\n  extraMounts:\n  - hostPath: '%s'\n    containerPath: /var/local-path-provisioner\n  extraPortMappings:\n  - {containerPort: 30080, hostPort: 5174, listenAddress: 127.0.0.1}\n" "${AIV_K8S_DATA//\'/\'\'}" > aiv-kind.yaml; if [ "$AIV_CGROUP" != cgroup2fs ]; then printf 'containerdConfigPatches:\n- |-\n  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]\n    SystemdCgroup = false\nkubeadmConfigPatches:\n- |-\n  apiVersion: kubelet.config.k8s.io/v1beta1\n  kind: KubeletConfiguration\n  cgroupDriver: cgroupfs\n' >> aiv-kind.yaml; fi; KIND_EXPERIMENTAL_PROVIDER=docker kind create cluster --name aivision --image kindest/node:v1.34.11 --config aiv-kind.yaml --wait 5m)
```

集群就绪后，按文档站步骤部署，context 使用 `kind-aivision`，namespace 使用 `aivision`；变量文件中设置 `PUBLIC_HOST: "127.0.0.1"`、`PUBLIC_PORT: "5174"`。本节映射的 `30080 → 5174` 同时提供 Web 与 OTLP HTTP 入口。

## 入口参数与实现边界

| 变量 | 默认值 / 用途 |
| --- | --- |
| `PUBLIC_HOST` | `127.0.0.1`；外部客户端可访问的 IP 或域名 |
| `PUBLIC_PORT` | `5174`；Web 和 OTLP HTTP 共用的对外端口 |
| `PUBLIC_SCHEME` | `http`；可选 `https` |
| `INGRESS_CONTROLLER` | `managed` 安装托管 Traefik；`existing` 复用已有 Controller |
| `INGRESS_CLASS` | `aivision`；复用 Controller 时设为它的 IngressClass |
| `INGRESS_HTTP_NODE_PORT`, `INGRESS_HTTPS_NODE_PORT` | 托管入口的 `30080`、`30443`；仅用于 Controller Service |
| `INGRESS_TLS_SECRET` | HTTPS 所需的同 namespace `kubernetes.io/tls` Secret 名称 |

托管 Controller 镜像固定为 `traefik:v3.7.13`，仅监听所属 namespace，不能跨 namespace 直接复用其 class。已有 Controller 必须监听目标 namespace，其公开地址和端口由集群管理员配置。HTTP 支持 IP 访问，使用 IP 时 Ingress 不设置 `host`。

HTTPS 的域名、证书和配置步骤见文档站。为新的 kind 集群提供 HTTPS 时，可在上面的配置中额外添加 `containerPort: 30443` → `hostPort: 443` 映射；保留 HTTP 的 `30080 → 5174`。托管 HTTPS 路由只绑定 HTTPS 入口，不自动配置 HTTP 重定向，也不自动申请证书。

## 更新、数据与能力边界

保留原 context、namespace 和配置重新执行 `aiv cast --target kubernetes ...` 更新；当前单副本应用使用 Recreate，更新可能中断服务。失败时资源保留，不自动回滚。当前固定路径只支持 `gauge`、`cast`、`status`、`logs`，没有 Docker 同形的 `destroy`／`redeploy`，集群备份和卸载由运维方管理。

不要复用整份 Docker vars 文件。当前 Kubernetes CLI 仅支持 OB Operator 管理的 OceanBase CE，不支持内置或外部 seekdb、外部 OceanBase，也不接受 `AIVISION_NATIVE_TRACE_HISTORY_START`，不能通过该路径启用原生 Langfuse v1 历史读取。需要此读取能力时使用 [Docker 部署](https://aivision.mintlify.site/zh-CN/deployment/docker)；SDK 上报与 v1 读取是不同能力。

本地管理状态默认位于 `~/.aivision/deploy/kubernetes/`，数据保存在集群持久卷；只保留 YAML 或本地状态不等于备份数据。网页可达后，继续使用真实 Agent 或 SDK 新记录验收采集与查询。
