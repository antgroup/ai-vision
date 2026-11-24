# 开发构建与版本发布验证

[部署文档](../README.md) · 简体中文 | [English](../en/dev-image-release.md)

[`Release images`](../../../.github/workflows/release-images.yml) 构建并验收固定的产品候选版。手动运行默认 **`publish=false`**，保留可审阅的制品和验收记录，不创建产品 Release，也不移动滚动标签。标准公开渠道使用源码兼容的服务镜像和普通 CLI，GitHub 仓库为 `antgroup/ai-vision`。当前产品目标为 `0.6.0`，实际可下载版本以 Releases 页面为准。

native 编译由[可选候选工作流](../../../.github/workflows/native-candidate.yml)负责。[临时私有 Skills 交付](../../../deploy/native-packaging/release-contract.md)保留自己的源码排除和访问门禁。用户 Skills 继续维护在 `ob-labs/ai-vision-skills`。定时 Daily 继续暂停。

## 发布渠道与测试范围

| 运行方式 | 来源与版本 | 验证通过后的产物 |
| --- | --- | --- |
| 手动 `daily`，默认 `publish=false` | 当前默认分支；`VERSION-daily.YYYYMMDD[.N]` | 冻结的候选制品和验收记录，不发布 Release 或产品 alias |
| 手动 `dev`，默认 `publish=false` | 当前默认分支；`VERSION-dev.YYYYMMDD.RUN_NUMBER[.ATTEMPT]` | 冻结的开发候选版，不发布 Release 或产品 alias |
| 手动试用 alias | 所选已推送分支；独立的非版本号 alias 和所选候选渠道 | 仅生成候选制品；发布会在写入版本标签之前被拒绝 |
| 合格 Daily／Dev 的推进发布 | 仍对应当前默认分支 head 的准确成功候选 run／attempt | 用原始冻结制品发布带版本号的预发布和滚动入口 |
| 手动 `release_preview=true` | 所选分支及准确的 `VERSION` | 执行正式发布验收并冻结制品，关闭发布 |
| 推送 `v*` tag | 与 `VERSION` 一致，且提交在默认分支历史中 | 通过受保护审批后的正式 Release 或 RC 预发布 |

显式 `publish=true` 的手动 Daily/Dev 运行仍可在相同源码／制品门禁通过后发布。独立推进流程用于先审阅候选、再发布，不会重新构建。

Daily 使用固定的快照日期。按 `YYYYMMDD` 设置 `snapshot_date`，重试或跨午夜时保留它。首个快照使用 `0.6.0-daily.YYYYMMDD`，当天第二个验收快照使用 `0.6.0-daily.YYYYMMDD.2`。重跑保留所选版本，run ID／attempt 用于区分构建身份。已验收的内容改变时，必须使用新的当日修订号。

正式 RC 使用 `0.6.0-rc.1` 作为 VERSION，Git 标签为 `v0.6.0-rc.1`。推送该标签会进入带审批的 RC 流程。验证 CI 时手动运行 Daily 或 Dev 分支试跑，GHCR 暂存标签按运行 ID 和尝试次数生成。

`snapshot_date` 留空时，CI 将原始工作流运行的 `created_at` 转为 Asia/Shanghai 日期，重跑也沿用。`daily_revision` 为正整数字符串，默认 `1`。手动 Dev 保留运行序号格式，正式版／RC 继续以 `VERSION` 为准。为分支试用选择 Daily 渠道不会绕过默认分支发布门禁。

PR 按改动路径选择 Linux 基础检查，包括版本和 release PR。集成检查需显式选择；full Daily 执行完整 Linux 源码验证，full release 调用扩展到已配置的多系统矩阵。见 [CI 说明](../../../scripts/ci/README.md)。源码测试、arm64 镜像编译均不代表对应平台已经完成制品部署验收。

## 候选验收与发布

1. 确定准确的源码 SHA 和已批准的上游 SigNoz 镜像索引 digest。正式版和 RC 读取[依赖锁文件](../../../deploy/config/release-dependencies.json)中对应基础版本的固定索引。公开候选将同一索引复制到 `ghcr.io/antgroup/ai-vision/data-backend` 并回读校验。
2. 确认所选源码通过基础 CI，再构建插件及配套普通 CLI。
3. 在 Linux amd64／arm64 构建器上构建变化的自有镜像，或验证从已发布 Release 复用的两个架构。记录实际构建身份和 digest，使用唯一候选 tag。
4. 生成带校验和的 CLI／制品、`release-manifest.json` 和两份固定变量文件。manifest 绑定 `source_repository`、`release_tag`、`product_version`、源码 SHA 和原始 build ID。
5. 验证五个公开候选镜像引用均可匿名读取。完整源码检查使用本次构建的自有镜像和固定的 SigNoz 精度测试镜像。制品验收使用打包后的 Linux amd64 CLI 和准确发布镜像，分别部署 OceanBase CE 和 seekdb，验证健康、认证、OTLP 写入和 API 回读（含原生记忆操作）、重新部署后的数据持久性、镜像身份及清理。
6. 将准确的制品大小／哈希及两份经过脱敏的成功 smoke 回执冻结到 `candidate-acceptance.json`。仅构建候选的运行在此结束。
7. 显式发布时，校验候选来源及当前远端状态，为五个公开镜像添加版本标签，再发布带版本号的 Release。下载并比较实际附件字节后移动滚动标签。正式版和 RC 另需已审阅的说明和受保护审批。

公开候选的 manifest 固定 `ghcr.io/antgroup/ai-vision/` 下的五个服务镜像。验收通过并显式发布后，`frontend`、`backend`、`ingest-gateway`、`diagnosis` 和 `data-backend` 均使用产品版本标签和滚动标签。`data-backend` 按 digest 固定获准使用的 `ghcr.io/ob-labs/signoz-ob` 镜像索引副本。`data_backend_mirror` 记录带有相同 digest 的上游和副本引用。SigNoz 维护上游标签及构建证据。OceanBase CE 和 seekdb 继续直接使用固定 digest 的上游镜像。

制品部署验收覆盖 **Linux amd64** 和两个数据库，不覆盖升级、Kubernetes、所有真实模型服务或未执行的平台。native 编译、镜像／CLI 平台可用性分别保留实际证据。

当临时 GitHub 托管 Linux x64 runner 的 SDK 所在文件系统可用空间少于 64 GiB 时，制品 smoke 仅清理未使用的预装 Android SDK，以释放磁盘空间。公开 smoke 会尝试使用新的 Docker 配置。如果该配置连接到原来的 Docker daemon，就用它执行部署，否则沿用 runner 的 Docker context。两种情况下，镜像仓库任务均独立验证候选镜像 digest 可匿名访问。

## 仓库配置与候选触发

在昂贵构建或发布前确认以下条件：

- Daily 和 Dev 可用 `AIVISION_SIGNOZ_IMAGE` 或手动 `signoz_image` 指定含两个 Linux 架构的 `ghcr.io/ob-labs/signoz-ob@sha256:...`。发布正式版本时，通过依赖锁文件评审 SigNoz 的镜像摘要、源码提交和构建回执。同一基础版本的 RC 和正式版使用同一份锁。
- 确认产品仓库已公开，并核对实际操作账户的 Package 和 Release 权限。即使 `publish=false`，候选也会向 GHCR 写入镜像数据。首次获授权的候选可能创建私有 Package，随后在部署 smoke 前停止。为五个 Package 配置仓库关联、工作流访问权限和公开可见性，再重跑候选，由镜像仓库门禁验证匿名访问。
- 正式版／RC：为 `release` environment 配置 required reviewers，禁止自我审批并关闭管理员绕过。按 [Release 模板](../../../.github/RELEASE_TEMPLATE.md)为准确的 tag 准备无附件的中英双语草稿。受保护的发布任务定位草稿并回读核验附件，通过后才发布。
- 可选的 `AIVISION_E2E_DOCKER_LINUX_RUNNER` 必须指向可信且足以运行完整系统的 Linux Docker runner。

发布工作流验收五个核心服务。可选 OpenShell 安装见[沙箱插件指南](sandbox-plugins.md)。

在 **Actions → Release images → Run workflow** 选择默认分支、`tag=daily`、固定日期和修订号，并使用 **`publish=false`**。`candidate_channel` 接受 `auto`、`daily`、`dev`。试用已推送的分支时，选择该分支，设置独立的 `tag` 和明确的候选渠道，并使用 `publish=false`。`latest`、版本号格式和自动生成的 `candidate-*` 名称被保留。候选按 Git ref 排队。更新镜像别名和 Release 的发布任务共用一个队列。

```bash
# 运行前填写实际快照日期／修订号。
SNAPSHOT_DATE=YYYYMMDD
gh workflow run release-images.yml -R antgroup/ai-vision \
  --ref main -f tag=daily -f publish=false \
  -f snapshot_date="$SNAPSHOT_DATE" -f daily_revision=1
gh run list -R antgroup/ai-vision --workflow release-images.yml --limit 5
```

此命令假设外部依赖变量已配置；否则另加 `-f signoz_image=IMAGE@sha256:DIGEST`，填写已确认的引用。新增工作流到达默认分支后，才可按名称手动触发。

发布计划记录 SDK Docker 覆盖范围和选择原因。上一份已发布基线可核验、
SigNoz 摘要相同且只修改允许范围内的文档或界面时，Daily 运行固定的 SDK
冒烟集。同次运行仍须通过完整 SDK 浏览器检查和制品部署验收。
完整覆盖的触发条件见 [CI 指南](../../../scripts/ci/README.md)。

## 打标签前预演正式发布

在待发布分支运行镜像工作流，设置 `release_preview=true` 和 `publish=false`，
保留 Daily 日期、修订和通道的默认值。工作流按准确 `VERSION`、固定的 SigNoz
依赖和正式发布矩阵验收。核对冻结回执后合入 `main`，在对应提交推送匹配
版本的标签。标签运行验收该次构建，通过后在 `release` 环境等待审批。

## 验收 Intel macOS Docker

正式发布必选双架构 Linux 数据库、Linux 平台和制品部署 smoke，以及现有的 macOS
和 Windows 可移植检查。Intel Mac 上依赖 Docker 的数据库、框架 SDK、诊断和原生
Agent 测试由独立手动工作流 [Intel macOS Docker](../../../.github/workflows/intel-macos-docker.yml)
验收。通过后可为对应候选记录这些源码检查的结果。制品部署验收覆盖 Linux amd64。

待工作流进入默认分支后，从候选源码检出目录执行。将 `SOURCE_REF` 设为指向清单中
`source_sha` 的分支或标签，将 `MANIFEST` 设为 **Release images** 工作流中已核验的候选
`release-manifest.json`。传入完整清单后，工作流会核对清单记录的源码 SHA 与实际检出提交，
并生成与正式 CI 相同的源码测试镜像列表，其中包含按摘要固定的 SigNoz 精度 fixture。

```bash
SOURCE_REF=...
MANIFEST=release-manifest.json
test "$(git rev-parse "${SOURCE_REF}^{commit}")" = "$(jq -er '.source_sha' "$MANIFEST")" &&
  gh workflow run intel-macos-docker.yml -R antgroup/ai-vision \
    --ref "$SOURCE_REF" -F "release_manifest=@$MANIFEST"
```

核对 Actions 运行的提交 SHA 与 manifest 一致，并把结果与候选回执一同留存。这个
Intel 工作流不发布镜像或 Release。OceanBase 在 Intel macOS 15 的 VZ、QEMU
以及 macOS 26 的 QEMU 对照运行中均在 600 秒后仍返回 MySQL 8001。独立门禁通过前，
Intel macOS Docker 仍待验收。详见 [CI 证据](../../../scripts/ci/README.md#test-tiers)。

## 验收 Windows 钩子性能

Windows 发布源码检查记录 Codex 和 Claude 的 PreToolUse p95。钩子功能、
原生进程、语义校验及 Windows x64 CLI 制品冒烟仍是发布必选。Linux 和 macOS
源码检查继续执行原有的 p95 门槛。

[Windows 性能工作流](../../../.github/workflows/windows-native-performance.yml)进入
默认分支后，在候选源码所属分支或标签上手动运行，并核对运行记录的提交 SHA。
该流程独立要求 Codex 内部 p95、Claude 内部和进程端到端 p95 均低于 50 ms。

## 验收 All-in-One 个人版

正式版和 RC 都包含内嵌 seekdb 的 Linux amd64 和 arm64 单容器个人版。Daily
可设置 `personal_edition=true` 和 `publish=false` 验收同样的双架构。正式预演也会
验证 Windows x64 CLI、个人版和分布式制品。原生双架构作业使用双库验收
通过后冻结的分布式候选，实际安装并验证镜像、CLI、首次使用和数据持久化。
两种架构均使用包内数据后端验证原生记忆操作回读。
最终的 `release-assets` 与 `release-acceptance` 保留分布式附件，加入个人版
安装器、清单、压缩包和验收凭证。晋级使用最终凭证，安装下载使用固定版本 Release。

## 审阅并推进冻结候选

成功候选上传 `release-assets-<attempt>`、`release-smoke-<attempt>-oceanbase`、`release-smoke-<attempt>-seekdb` 和 `release-acceptance-<attempt>`。最后一个制品包含 `candidate-acceptance.json`；smoke 制品仅包含经过脱敏的结构化回执，不包含原始日志或私密运行配置。

审阅成功候选并授权发布后执行：

```bash
CANDIDATE_RUN_ID=...
CANDIDATE_ATTEMPT=...
gh workflow run promote-release.yml -R antgroup/ai-vision \
  --ref main -f candidate_run_id="$CANDIDATE_RUN_ID" \
  -f candidate_run_attempt="$CANDIDATE_ATTEMPT"
```

推进要求官方 `release-images.yml` 的最新成功 attempt，且源码仍为**当前默认分支 head**；两个数据库 smoke job 均成功，制品与冻结记录完全相符。新的重跑会取代旧验收。过期、外仓、失败的 attempt，改变的制品、分支试用及正式版／RC 候选均被拒绝。制品保留原始 candidate build ID；发布器记录自己独立的 run／attempt，并上传 `promotion-receipts-<publisher-run-id>-<attempt>`，不会替换 GitHub 运行身份。

## 下载并部署被测组合

首次公开安装从[正式 Release](https://github.com/antgroup/ai-vision/releases/latest)选择版本，需要试用时通过 [Daily 入口](https://github.com/antgroup/ai-vision/releases/tag/daily)选择对应的带版本号预览。配套 CLI、`SHA256SUMS`、`release-manifest.json` 和所选变量文件须来自同一版本。OceanBase CE 使用 `release-images.vars.yaml`，seekdb 使用 `release-seekdb.vars.yaml`。校验文件后，将所选变量文件复制到 `deployment.vars.yaml`，填写主机所需配置，再运行 `aiv cast --vars-file ./deployment.vars.yaml`。该文件中的 digest 用于拉取公开镜像。新安装直接运行 `aiv cast` 会使用五个公开镜像包的滚动 `latest` 标签。

如需试用已获授权的候选，下载该候选的 Actions 制品。填写实际通过验收的 run ID 和 attempt：

```bash
RUN_ID=...
ATTEMPT=...
gh run watch "$RUN_ID" -R antgroup/ai-vision --exit-status
gh run download "$RUN_ID" -R antgroup/ai-vision \
  -n "release-assets-$ATTEMPT" -D release-assets
cd release-assets
sha256sum -c SHA256SUMS
tar -xzf aiv-linux-x64.tar.gz aiv
install -d "$HOME/.local/bin"
install -m 755 aiv "$HOME/.local/bin/aiv"
export PATH="$HOME/.local/bin:$PATH"
command -v aiv
aiv --version
cp release-images.vars.yaml deployment.vars.yaml
chmod 600 deployment.vars.yaml
```

命令面向 Linux amd64。先完成[持久 PATH 配置与新终端验证](../../../aivision-cli/README.md#make-aiv-available-in-new-terminals)，再按 [Docker 说明](docker.md)补充密钥和环境配置，保留镜像 digest。将配置与 manifest 保存在持久的私密目录。试用需要隔离主机，或[独立的名称、端口及状态](../../../aivision-cli/README.md#running-an-isolated-second-stack-same-host)。

```bash
aiv cast --namespace aivision --vars-file deployment.vars.yaml
aiv status --namespace aivision
aiv logs --namespace aivision -n 80 backend
```

后续操作保留实际 target、namespace、workdir、blueprint。修改配置后重复同一 `cast`；授权重建使用 `aiv redeploy --namespace aivision`，保留数据停止使用 `aiv destroy --namespace aivision`。

升级已有部署时，在原私有变量文件中更新五个镜像引用和发布元数据，保留数据库类型及配置、凭据、namespace 和存储目录。若已使用 `DATA_BACKEND_IMAGE` 等角色键，请直接修改该键。同一文件中的角色键优先于 `SIGNOZ_IMAGE` 等旧键。执行 cast 前还需检查环境变量和命令行覆盖项。关于页面按 manifest 核对实际组件，复用镜像仍显示原始 SHA 和版本。


如果现有 OpenShell 部署还会写入无密钥的注册凭据指纹，先升级到写入带密钥指纹、
仍兼容旧重试的后端（[由 #8 引入](https://github.com/antgroup/ai-vision/pull/8)）。
最后一个写入无密钥指纹的后端实例停止服务后，至少等待 60 分钟，再升级到要求带密钥
指纹的后端。如果跳过过渡版本，至少提前 60 分钟暂停签发 OpenShell 安装命令，
等后端升级完成再恢复签发。保留已保存的连接及其主机归属状态。旧命令无法确认注册时，
在 [OpenShell 安装侧栏](sandbox-plugins.md#推荐在-ai-vision-部署主机上自动安装)重新生成命令。

## 复用、失败与恢复

默认复用基线是最近发布的带版本号产品 Release，`reuse_release` 可明确指定。CI 校验 manifest、校验和和源码 tag。构建上下文未变时可保留镜像 digest 与真实原始身份；元数据缺失或输入改变时重新构建。`force_rebuild=true` 用于刷新构建／基础镜像。源码测试和部署验收结论始终重新获取。

源码检查独立于发布准备。缺少 SigNoz 配置或镜像仓库权限会阻止制品生产，源码回归仍运行。源码 fixture 的 SigNoz v1 回读不等于候选的原生 Langfuse v1 回读。

候选失败后使用 **Re-run all jobs**，确保 CLI、镜像和检查属于同一 attempt。Daily 保留快照日期／修订号；已接受内容改变时使用下一个修订号。失败后唯一候选镜像 tag 可能保留，仅构建候选的运行始终不移动用户 alias。

Dev alias 附件上传中断后，可以用原候选的 run／attempt 重试推进发布。直接 `publish=true` 的候选若仅在开发发布作业失败，需设置 `recover_dev_publication=true`；校验器要求同一 attempt 的两个 smoke 和冻结验收均已通过，其他失败或取消仍会被拒绝。恢复会验证冻结验收、已发布的带版本制品、原 Dev 构建／源码入口和当前 tag，再恢复同一组字节。原候选说明保留，恢复发布器单独记录。较新、变化或来源无法验证的 alias 状态需要先检查。新的 Dev 发布会先回读上传后的附件，再写入完成说明。

发布中断时保留回执，恢复前检查准确制品、digest 及当前远端状态。不能在已接受版本下重建不同字节、覆盖更新／来源不明的 alias，或手动绕过拒绝门禁。发布涉及多个 GitHub／镜像仓库写入，不是原子操作。账单／权限失败、runner 不可用和必要检查跳过都属于失败或缺少证据。

推进 `daily` 时，workflow attempt 必须更新，产品版本也必须保持单调。发布器会通过已发布的带版本 Release、经认证读取的 manifest／校验和及当前 tag 核实既有版本，再按数字基础版本、快照日期和修订号比较。较晚运行不能覆盖更新日期或修订号；历史来源缺失或不一致时停止发布。

独立的手动 Product Compose lifecycle 要求 Docker 至少有 14 GiB 内存。默认 runner 容量不足时，将 `AIVISION_E2E_DOCKER_LINUX_RUNNER` 配置为容量足够的 Linux x64 runner。容量检查在 checkout 和构建前执行。
