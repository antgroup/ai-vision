# 纯 CLI 接入验收记录

日期：2026-09-18。对象：本次未提交工作区中的 `aiv`、服务端 Python 安装器与发布配置。

## 英文 CLI 端到端复测（最新）

2026-09-18 12:43（Asia/Shanghai）完成修复后的全新账号验收。使用独立 OceanBase 数据库、临时后端镜像、全新的 CLI 配置目录和 Codex 配置目录；复用已有采集网关及 SigNoz。客户端操作全部通过 `aiv`，没有网页生成步骤，没有模拟遥测。

本次先复现并修复了一个真实问题：后端将 SpaceKey 缓存在进程内 60 秒，已撤销的 Key 仍可能通过鉴权，CLI 导入的 Key 因此也不能立即清理。新建 Key、禁用 Key 和空间状态变化同样会受到缓存影响。

修复位于 [key_cache.py](../../ai-vision/aivision-py-be/apps/core/services/key_cache.py)：SpaceKey 鉴权按每次请求查询数据库当前状态，使用已有的唯一 `key_hash` 索引；保留 `last_used_at` 批量写入。这样其他 worker 也能立即看到已提交的撤销操作，代价是每次 SpaceKey 鉴权增加一次索引查询。[回归测试](../../ai-vision/aivision-py-be/apps/core/tests/test_space_key_lifecycle.py)覆盖两个独立鉴权缓存实例上的新建、禁用/启用、撤销和空间状态变更；4 项测试在修复前失败、修复后通过。

| 真实链路检查 | 结果 |
|---|---|
| 注册、自动登录、个人空间选择 | 通过；密码未写入 CLI 配置，配置权限 `0600` |
| 创建团队空间、重新登录、空间切换 | 通过；恢复选中空间，不混用空间 Key 和 Agent 上下文 |
| `connect --dry-run` | 通过；服务端 Agent/授权数量及本机原生配置均未改变 |
| 首次接入、重复接入 | 通过；不要求查询 Key，复用 Agent UUID，五个 hook 没有重复登记 |
| Key 创建、列表、导入 | 通过；终端仅显示脱敏 Key |
| 会话过期与并发续期 | 测试服务端 access TTL 为 10 秒；过期后 6 个 CLI 进程全部成功，数据库只新增 1 代 refresh token |
| 真实宿主任务 | Codex `0.154.0` 原生界面审查并信任五个 hook；通过 shell 读取 `19` 和 `23`，返回 `42`，输入文件未修改 |
| 数据查询 | 任务前 `no_recent_data`，任务后 `found`；1 条 trace、4 个 span、0 个错误 |
| 跨空间数据隔离 | 个人空间另行接入 Codex 后查询为空；切回团队空间自动恢复上下文并查到本次 trace |
| Key 撤销 | 旧 Key 立即返回 HTTP 401；导入的本地 Key 自动清除；Agent 上报凭据仍有效 |
| 账号退出 | 尚未过期的 access Cookie 被服务端拒绝，本地 profile 删除；Agent 仍可申请 ingest token |
| 英文文案 | 捕获的 CLI 和安装器输出全部为英文，覆盖成功、无数据、缺少 Key 和退出后的报错 |

未接入 Codex 的空间查询该产品会返回 `Agent query route not found`；因此跨空间隔离验证先在个人空间完成单独接入，再断言其查询结果为空。

修复后的镜像为 `sha256:8be92b7f27876e0fad94a1fbaf659fc5cd6e6932d58d9a8e83588bc987e73873`。本次全量构建在 Python 依赖下载阶段取消，改为复用现有后端镜像的依赖层；核对其 46 个已安装包的版本均匹配当前锁文件，再打入当前后端代码、安装器和 hook 产物。已核对运行镜像的安装器、实际下载脚本和 SpaceKey 鉴权代码与工作区一致。

修复后的非敏感 trace 证据：

```json
{
  "space_id": "ccc7091c-fece-426d-9167-e5227b4aa939",
  "agent_uuid": "00c43123-caff-480c-bc29-d70b82307ca1",
  "agent_product": "Codex",
  "session_id": "01a0b2d3-0e5f-7c62-9d35-678bdd7e7f77",
  "trace_id": "ade140b075765ec0de2e733a9a577daa",
  "span_count": 4,
  "error_count": 0,
  "start_time_unix_nano": "2026-09-18T04:43:13.615Z"
}
```

本轮验证通过：`go test -mod=vendor ./internal/account ./internal/cli`、21 项安装器测试、60 项后端测试（包括新增的 4 项 SpaceKey 生命周期回归）。后端测试使用 SQLite，真实 CLI 验收使用 OceanBase。其他平台、其他宿主 Agent 未进行实机复测。

脱敏 JSON 证据和 CLI 输出保存在本地 `aivision-cli/dist/e2e/2026-09-18/cli-onboarding.{json,log}`（Git 忽略目录），共记录 14 项通过的端到端检查。验收账号的查询 Key 和账号会话已撤销，验收专用 SigNoz Key 已撤销；临时容器、数据库、镜像、宿主配置及测试凭据均已清理。已有部署未被替换，修复需要通过后续后端镜像构建部署生效。

## 首轮实际运行结果

Linux x64 上已完成实际镜像、新账号、隔离客户端目录和真实 Codex 任务的端到端验收，没有通过网页生成安装命令，也没有调用模拟遥测脚本。

| 项目 | 结果 |
|---|---|
| CLI | `aiv 1.0.0`，从本工作区构建 |
| 后端 | 从仓库 Dockerfile 构建 `local/aivision-be:cli-e2e`；独立临时容器与 OceanBase 数据库 |
| 真实任务时的后端镜像 ID | `sha256:0508ad3821e559b7621512932e9dd90000c99e45c9da70648a17cffe0f9ce67f` |
| 遥测服务 | 已运行的 `local/antcollector-gw:dev`、`local/signoz-ob:dev` |
| 宿主 Agent | Codex CLI `0.154.0`，使用隔离配置目录 |
| 安装产物 | 镜像内 `cdxhooks 0.3.17`，Linux GNU/x64，下载及 SHA-256 校验通过 |
| 真实任务 | 通过 shell 读取 `numbers.txt` 中的 `17` 与 `25`，输出 `42`；未修改业务文件 |
| 本机检查 | `installed=true`、`registration=verified`、`credential=valid` |
| 查询检查 | `data_status=found`；新空间下返回 1 条 Codex trace、4 个 span |

补充本机产物损坏检查后，基于上述已验证镜像覆盖 `scripts/connect`，构建复测镜像 `sha256:7a0db11569c423fa0bfd82c325f76ebba4b9e3bea92760b6e634ee90e7e55548`；重复接入、五个 hook 去重和真实 trace 状态再次通过。后续对状态检查中“下载脚本的默认地址也应参与空间凭据范围校验”的修正由 Python/CLI HTTP 测试覆盖。未把同时发生的依赖源锁文件修改纳入本轮代码修改；二次全量镜像构建的依赖下载被取消，复测使用前述已验证的依赖层。

终端流程：

```bash
aiv auth register --endpoint http://127.0.0.1:8015 --email <new-test-account> \
  --name 'CLI Acceptance' --password-stdin
aiv auth status
aiv space list
aiv connect codex --dry-run --config-dir <isolated-codex-dir>
aiv connect codex --config-dir <isolated-codex-dir>
aiv key create --name cli-acceptance

# 重启隔离配置下的 Codex，在原生 hooks 界面审查并信任本次安装的 5 个 hook。
# 执行上述真实任务，再查询。
aiv agent status codex --format json
aiv query trace list --since 1h --limit 5 --format json
```

本次 trace 的非敏感证据：

```json
{
  "space_id": "4082bcef-8c98-4d59-9e7d-ba61b8c2c185",
  "agent_uuid": "2adde5a9-bc8c-4511-9b6b-3aef965c0c4d",
  "agent_product": "Codex",
  "session_id": "01a0b2a7-c3b7-7411-9d2b-9b5ad7f35ea1",
  "trace_id": "50a834d72802853c8d0789a255b5e10f",
  "span_count": 4,
  "error_count": 0,
  "start_time_unix_nano": "2026-09-18T03:56:38.454Z"
}
```

验收时，原测试部署的 AI Vision 数据库记录了另一套 SigNoz 的组织/Key，查询返回 401。为避免改变已有业务配置，本次建立独立验收数据库，通过现有 `configure_third_party_agents` 和 `bootstrap_signoz_integration --org-id …` 接入当前 SigNoz，随后查询通过。原部署的这个配置不一致问题没有被本次修改覆盖。

临时账号查询 Key 和验收专用 SigNoz Key 已撤销，账号会话已退出；隔离容器、验收数据库、本机宿主配置、测试凭据及临时镜像均已清理。上述 ID 是验收证据，不是留给用户使用的持久账号。

## 自动验证

| 检查 | 结果与范围 |
|---|---|
| `go test -race -mod=vendor ./...` | 通过；Cookie/CSRF、账号/空间隔离、旧 profile、查询凭据隔离与安装链路 |
| 并发刷新 | 6 个独立子进程同时读取过期会话，只发生 1 次 refresh；锁内重读和保存 |
| 登录中断 | 刷新成功但空间读取失败时仍保存新 Cookie，后续命令不重放旧 refresh token |
| CLI HTTP 集成 | 新账号、空间创建/选择、Key 创建/导入/撤销、镜像安装器、canonical `base_url`、默认查询上下文、状态/退出 |
| 安装异常 | 无平台产物、授权过期、确认失败回滚、同授权重试、重复安装不重复 hook、凭据撤销；dry-run 不改服务端或本机配置 |
| `go vet -mod=vendor ./...` | 通过 |
| `make test-onboarding`（Python 3.13） | 21 个测试通过，包含状态检查只读、凭据分域、checksum 和产物架构校验 |
| 安装脚本测试 | 四个平台识别、版本固定、SHA-256 错误时保留旧程序、原子替换通过；平台识别测试使用 fixture |
| Django 认证边界 | 镜像内运行 `test_local_auth`、`test_agent_installation`、`test_third_party_onboarding`、`test_download_latest`、`test_url_auth_boundaries`、`test_space_permissions`；40 个测试通过，使用测试 SQLite 数据库 |
| 现有 hook 产物 | 三个 Linux GNU/x64 二进制及四个通用 Node 包的 checksum、格式和包标识检查通过 |
| 发布 YAML | 三份工作流语法解析通过；尚未触发远端 Actions 或发布 Release |

## 平台验证边界

| 平台 | CLI 构建、架构与压缩包校验 | 本机启动 | 对应 hook 产物 | 真实任务 |
|---|---|---|---|---|
| Linux x64 | 通过 | 通过 | 通过 | Codex 通过 |
| Linux arm64 | 交叉编译与 SHA-256 校验通过 | 未运行 | 当前本地 dist 未提供；待 CI 构建 | 未运行 |
| macOS x64 | 交叉编译与 SHA-256 校验通过 | 未运行 | 当前本地 dist 未提供；待 CI 构建 | 未运行 |
| macOS arm64 | 交叉编译与 SHA-256 校验通过 | 未运行 | 当前本地 dist 未提供；待 CI 构建 | 未运行 |

`.github/workflows/release-cli.yml` 为四种平台分别配置原生 runner，执行 CLI 测试、启动、压缩包解包启动及对应 hook 清单验证；镜像发布工作流先构建并合并完整 hook dist，再复用 CLI 工作流。runner 标签按 [GitHub 官方列表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 选择。

本次没有把交叉编译、fixture 测试或 CI 配置计作其他平台的实际运行证据。其他六类宿主 Agent 的真实任务验收也未执行；现有安装器回归与本次状态检查测试不能替代它们。

本地四平台压缩包与 `SHA256SUMS` 保留在 `aivision-cli/dist/cli-release/`（Git 忽略目录），当前 Linux 可执行文件为 `aivision-cli/aiv`。这些产物未上传或发布；本地安装脚本仍需指定 `--repository OWNER/REPO`，正式工作流会自动填充发布仓库。
