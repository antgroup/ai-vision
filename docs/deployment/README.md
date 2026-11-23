# 部署入口与维护资料 / Deployment and maintenance

先在[部署概览](https://aivision.mintlify.site/zh-CN/deployment/overview)选择部署方式，再按下表对应的指南安装和验收。

Start with the [deployment overview](https://aivision.mintlify.site/en/deployment/overview) to choose a deployment method, then follow the corresponding guide below to install and verify it.

部署完成时，`aiv` 应保留在固定安装目录并能在新终端直接执行。后续维护继续使用 CLI；保留原 target、namespace、workdir、blueprint、配置文件与 Compose file/env 参数。旧安装器仅打印 PATH 提示时，按 [持久 PATH 步骤](../../aivision-cli/README.md#make-aiv-available-in-new-terminals)补齐并验证，不需要额外的 `manage` 包装脚本。

Deployment leaves `aiv` in a permanent installation directory and verifies it in a fresh terminal. Continue maintenance through the CLI with the original target, namespace, workdir, blueprint, configuration and Compose file/env selectors. When an older installer only prints PATH guidance, complete the [durable setup](../../aivision-cli/README.md#make-aiv-available-in-new-terminals); no `manage` wrapper is required.

| 用户指南 / User guide | 简体中文 | English | 日本語 |
|---|---|---|---|
| 安装 CLI / Install the CLI | [安装](https://aivision.mintlify.site/zh-CN/guides/install-cli) | [Install](https://aivision.mintlify.site/en/guides/install-cli) | [インストール](https://aivision.mintlify.site/ja/guides/install-cli) |
| 个人部署 / Personal All-in-One | [部署](https://aivision.mintlify.site/zh-CN/deployment/all-in-one) | [Deploy](https://aivision.mintlify.site/en/deployment/all-in-one) | [デプロイ](https://aivision.mintlify.site/ja/deployment/all-in-one) |
| Docker | [部署](https://aivision.mintlify.site/zh-CN/deployment/docker) | [Deploy](https://aivision.mintlify.site/en/deployment/docker) | [デプロイ](https://aivision.mintlify.site/ja/deployment/docker) |
| Kubernetes | [部署](https://aivision.mintlify.site/zh-CN/deployment/kubernetes) | [Deploy](https://aivision.mintlify.site/en/deployment/kubernetes) | [デプロイ](https://aivision.mintlify.site/ja/deployment/kubernetes) |

产品定义与验收入口统一在根 [deploy/](../../deploy/README.md)。CLI 的旧内嵌路径和本目录旧变量示例是生成兼容副本；维护时修改权威源并运行 `make assets`。默认 Docker 保持 Go 运行时；Compose 与 Kubernetes 可选。当前源码提供 `aiv ... --target compose`，通过 Go 调用原生 Compose，无额外宿主 Python 控制层。发布版是否包含该入口须按实际版本确认。

Product definitions and acceptance entry points are owned by root [deploy/](../../deploy/README.md). Old embedded paths and the old vars example are generated compatibility copies. Edit canonical sources and run `make assets`. Docker through Go remains the default; Compose and Kubernetes are optional. Current-source aiv --target compose invokes native Compose through Go, without host Python control scripts. Verify this capability in the actual release.

Helm 部署已确定为后续方向，目前不可用，本次不提供 Chart 或 Helm 运行命令。当前 Kubernetes 仍使用固定 CLI 入口；未来配置映射、初始化/升级与持久资源所有权边界见 [Helm roadmap](../../deploy/kubernetes/helm-roadmap.md)。

Helm is a committed follow-up and currently unavailable; this delivery provides no Chart or runnable Helm commands. Kubernetes continues to use the fixed CLI path. The [Helm roadmap](../../deploy/kubernetes/helm-roadmap.md) records future configuration, initialization/upgrade and persistent-resource ownership boundaries.

## 仓库内保留的资料 / Repository maintenance material

以下资料随部署实现维护，不重复文档站的基础步骤。

These files track the deployment implementation and supplement the user guides.

| 资料 / Material | 简体中文 | English |
|---|---|---|
| Docker 维护补充：历史扫描、网络、代理 / History scanning, networks and proxies | [补充说明](zh-CN/docker.md) | [Operational notes](en/docker.md) |
| Kubernetes：本地 kind 与维护边界 / Local kind and operational limits | [补充说明](zh-CN/kubernetes.md) | [Operational notes](en/kubernetes.md) |
| 原生轨迹与会话：自动维护与恢复 / Native query maintenance and recovery | [运维说明](zh-CN/trace-read-model.md) | [Operational guide](en/trace-read-model.md) |
| 分支镜像与开发包发布 / Branch images and development artifacts | [发布说明](zh-CN/dev-image-release.md) | [Release guide](en/dev-image-release.md) |
| 可选 Sandbox 插件：安装、连接与租约清理 / Optional Sandbox plugin installation and lifecycle | [OpenShell 插件](zh-CN/sandbox-plugins.md) | [OpenShell plugin](en/sandbox-plugins.md) |

## Agent 实现索引 / Implementation index

| 要定位的信息 / Task | 仓库入口 / Repository source |
|---|---|
| 产品 Compose / Product Compose | [入口与四种数据库组合](../../deploy/docker/README.md)、[生成与验收边界](../../deploy/README.md) |
| 组件角色与兼容 / Roles and compatibility | [命名表](../../deploy/README.md#组件命名与兼容)、[machine mapping](../../deploy/components.json) |
| Docker 数据库选择与版本边界 / Datastore selection | [CLI reference](../../aivision-cli/README.md#choose-the-docker-datastore)、[中文补充](zh-CN/docker.md#use-seekdb)、[English notes](en/docker.md#use-seekdb) |
| 镜像与源码蓝图 / Image and source blueprints | [aivision.yaml](../../deploy/blueprints/aivision.yaml)、[aivision-source.yaml](../../deploy/blueprints/aivision-source.yaml) |
| 数据库首启、凭证与持久化 / Datastore lifecycle | [default Docker runtime](../../aivision-cli/internal/deploy/runtime/datastore.go)、[tests](../../aivision-cli/internal/deploy/runtime/datastore_test.go)、[optional Compose Go controller](../../aivision-cli/internal/deploy/compose/) |
| Kubernetes 参数与能力 / Kubernetes config | [config.go](../../aivision-cli/internal/deploy/kubernetes/config.go)、[implementation and tests](../../aivision-cli/internal/deploy/kubernetes/) |
| seekdb 部署冒烟 / Deployment smoke | [script](../../deploy/tests/seekdb_deploy_smoke.py)、[workflow](../../.github/workflows/seekdb-deploy-smoke.yml) |

Docker 默认并推荐使用 OceanBase CE，适合团队部署与持续增长的观测数据；内置或已有的 OceanBase seekdb 面向个人、本地开发和轻量使用。`OB_BACKEND=seekdb` 需要包含此功能的 CLI，发布前从源码构建。Kubernetes 当前仅支持 OB Operator 管理的 OceanBase CE。完整支持范围和限制见上表的 CLI 参考。

Docker defaults to OceanBase CE, the recommended datastore for team deployments and growing observability workloads. Managed or existing OceanBase seekdb is the lightweight option for individual use and local development. `OB_BACKEND=seekdb` requires a CLI containing this feature; build from source until released. Kubernetes currently supports only OceanBase CE managed by OB Operator. See the CLI reference above for source-version limits.

共享 [数据库配置示例 / database configuration example](../../deploy/config/aivision.vars.example.yaml)。源码构建见 [CLI source build](../../aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch)。返回[仓库知识索引](../README.md)。
