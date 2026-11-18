# 纯 CLI 注册、接入和查询

安装 `aiv` 后，用户可以在终端完成账号注册、空间选择、hook 接入和数据查询。部署服务与客户端接入是两个步骤；使用已有部署的用户不需要执行 `aiv cast`。

## 安装客户端

从部署方对应的 GitHub Release 下载 `install-aiv.sh`，执行：

```bash
sh install-aiv.sh                     # 默认 ~/.local/bin，GitHub latest 正式发布
sh install-aiv.sh --version dev       # 选择名为 dev 的滚动发布
sh install-aiv.sh --version vX.Y.Z    # 固定版本；替换为真实版本号
export PATH="$HOME/.local/bin:$PATH"
aiv --version
```

安装还需完成持久 PATH 配置并在新终端验证 `command -v aiv` 与 `aiv --version`。zsh 使用 `${ZDOTDIR:-$HOME}/.zshrc`；Bash 同时配置 `.bashrc` 与实际生效的登录启动文件，不能只改未被读取的 `.profile`。完整步骤见 [新终端直接使用 aiv](../README.md#make-aiv-available-in-new-terminals)。源码中的新版安装器会默认配置 Bash/zsh，并支持 `--no-modify-path`；已发布的旧安装器可能只打印 export，不能假定包含新选项。安装脚本无法修改父终端，当前终端仍需上面的 export。

`--version 1.2.3` 与 `--version v1.2.3` 都选择 `v1.2.3`；`dev` 等命名标签按原值使用。默认 `latest` 不选择预发布，只有预发布时需显式指定标签。

发布包为 `aiv-{linux,darwin}-{x64,arm64}.tar.gz`，附带 `SHA256SUMS`。安装脚本按本机平台下载、校验 SHA-256，再替换可执行文件。源码中的 `scripts/install.sh` 需要额外指定 `--repository OWNER/REPO`；发布时会填入仓库名。也可离线 `make build` 后自行安装。

## 从新账号开始

```bash
# 输入姓名和隐藏的密码；注册成功自动登录并选择个人空间。
aiv auth register --endpoint https://vision.example.com --email you@example.com

# 已有账号：输入隐藏的密码。
aiv auth login --endpoint https://vision.example.com --email you@example.com

aiv auth status
aiv space list
# 可选：选团队空间，或创建并选择一个新团队空间。
aiv space use <space-uuid>
aiv space create --name 'My team'

# 先检查当前机器的 Python、宿主 Agent 和镜像提供的平台产物。
aiv connect codex --dry-run
aiv connect codex

# 重启 Codex；在 /hooks 检查和信任新增 hook，然后执行一次真实任务。
aiv agent status codex

# 查询单独使用 SpaceKey。管理员可以创建，其他成员可导入获授权的 Key。
aiv key create --name cli
# 或：aiv key import                  # 隐藏输入已有 SpaceKey

aiv query trace list --since 1h --limit 5
aiv agent status codex
```

`connect` 支持 `claude-code`、`codex`、`kimi`、`opencode`、`openclaw`、`pi`、`dsh`；别名与原安装器保持兼容。首次接入不需要填写 Agent UUID、安装 token 或 SpaceKey。CLI 先检查目录和平台产物，再调用空间 Agent 创建/复用接口、签发一次性安装授权，并使用授权响应中的 `base_url` 运行已有安装器。产物从部署镜像下载并校验 SHA-256。

重复接入复用空间内同类型 Agent，并保留原有 hook 去重和配置回滚。CLI 仅在安装及服务端确认成功后保存默认查询 Agent。安装失败可重新运行同一 `aiv connect`；旧显式命令使用相同已兑换授权重试时，可复用安装器保存的 Agent 凭据。

`--dry-run` 不创建 Agent、不签发安装授权、不兑换凭据、不写原生配置；只读取目录和检查宿主命令/Python/平台。它不能证明凭据有效、宿主会执行 hook 或数据已上报。

## Full 与 Privacy

首次 `aiv connect <agent>` 默认 Full；重复接入保留本机已保存的模式。明确指定 `--collection-mode privacy` 开启 Privacy，指定 `--collection-mode full` 切回 Full；参数不区分大小写。两种模式都不要求先创建 SpaceKey。

```bash
aiv connect codex --collection-mode privacy --dry-run
aiv connect codex --collection-mode privacy
# 重启宿主，开始一个新会话并执行真实任务。
aiv agent status codex
```

网页继续生成现有显式安装命令，可在终端追加同一个 `--collection-mode` 参数。网页不提供模式选项或状态。详见 [Privacy 配置与验收](privacy.md)。

## 账号会话、查询 Key、上报凭据

| 用途 | 凭据 | 保存与管理 |
|---|---|---|
| 账号、空间、Agent 管理 | Cookie 会话 + CSRF | CLI 的私有 `config.json`；自动续期 |
| 数据查询 | 空间级 SpaceKey | 按 profile 和空间保存；`key create/import/list/revoke` |
| Agent 上报 | 安装授权换取的 Agent credential + 短期 ingest token | 宿主配置目录下的 `aivision/<agent>/ingest.json`、`ingest-token.json`；安装器/runtime 管理续期 |

账号请求不发送 SpaceKey Bearer，查询请求不发送账号 Cookie，密码不落盘。SigNoz 的内部 Key 留在后端，不需要用户填写。

`auth status` 请求服务端校验会话；SpaceKey-only profile 则在线校验 Key。`auth logout` 先撤销账号会话，再清理对应本地 profile（包括其查询上下文）；网络失败会保留配置以便重试。退出账号不撤销已安装 Agent 凭据，也不撤销服务端 SpaceKey。

```bash
aiv key list              # 只显示 ID、名称、脱敏前缀和启用状态
aiv key revoke <key-id>    # 服务端撤销查询 Key，清除匹配的本地 Key
aiv auth logout
```

服务端已有 Key 不可恢复原文，需要导入已有原文或创建新 Key。创建 Key 后 CLI 立即保存，不打印原文。Key 管理权限沿用后端：管理员可创建/撤销；Agent 接入权限与 Key 管理权限独立。

## 多账号、多平台、多空间

```bash
aiv auth login --profile work --endpoint https://work.example.com --email you@example.com
aiv auth login --profile lab --endpoint https://lab.example.com --email you@example.com
aiv space list --profile work
aiv space use <space-uuid> --profile work
aiv connect claude-code --profile work
aiv query trace list --profile work
```

成功登录的 profile 成为 active profile。命令接受 `--profile`，也可设置 `AIVISION_PROFILE`。每个 profile 绑定一个平台地址和账号；在同名 profile 登录不同账号或平台时，不继承旧账号的空间 Key。需要同时保留多个账号时使用不同 profile 名称。

同一账号重新登录会恢复仍有权限访问的选中空间，否则选择个人空间。每个空间分别保存查询 Key 和最近成功接入的 Agent；切换空间不复制 Key。旧 flat profile 会迁移到它原有的 `spaceId`，原 SpaceKey 查询模式继续可用。

查询参数优先于 profile；指定其他空间时，只使用该空间已经保存的 Key。指定其他平台时需显式提供该平台的 Key。存在 profile 时，缺失的空间 Key 不会从父 shell 的旧环境变量补齐；没有 profile 的旧环境变量模式仍支持。`--space-key` 可显式覆盖。

配置根目录默认为 `~/.aivision`，可通过 `AIVISION_CONFIG_DIR` 隔离。`config.json` 使用 `0600` 权限和原子替换；跨进程文件锁覆盖会话重读、刷新和保存，避免并发使用旧 refresh token。不要手动复制一个活动会话到不同机器并同时刷新。

## 无交互使用和旧命令

```bash
# 密码从 stdin 读一行；注册必须显式提供姓名。
aiv auth register --endpoint https://vision.example.com --email bot@example.com \
  --name 'CLI user' --password-stdin < /secure/password.txt

aiv auth login --endpoint https://vision.example.com --email bot@example.com \
  --password-stdin < /secure/password.txt

aiv key import --key-stdin < /secure/space-key.txt

# 保留旧版查询模式；不能与 --email/--password-stdin 混用。
aiv auth login --endpoint https://vision.example.com \
  --space-key sk-aiv-... --space <space-uuid> --agent-product Codex \
  --no-env-file --no-shell

# 网页生成的完整安装命令继续有效。
aiv connect codex --base-url https://vision.example.com --space-id <space-uuid> \
  --agent-uuid <agent-uuid> --installation-token <one-use-token>
```

无终端时不隐式读取交互密码；缺少姓名、密码、空间 ID 等必要参数会直接报错。不要把密码传入命令行参数。

旧 SpaceKey 登录仍支持 `--no-verify`、`--no-env-file`、`--no-shell`。默认可以写入 `env.sh` 和 shell source 块；账号登录无需这些文件。`auth print-env` 保留显式导出查询凭据的能力，输出含 Key 原文，只用于本机 shell。空间切换会同步已有 `env.sh`；已运行的父 shell 不会自动变化，CLI 本身每次重新读取 profile。退出后如曾手动导出凭据，可在当前 shell 执行 `unset AIVISION_PROFILE AIVISION_SPACE_KEY AIVISION_SPACE_ID AIVISION_AGENT_PRODUCT AIVISION_ENDPOINT`。

## 状态检查的含义

`aiv agent status <agent> [--config-dir DIR] [--format json]` 调用同一 Python 安装器解析配置目录，分别检查：

- 本机保存模式及配置来源；当前检查进程有环境覆盖时单独显示，不能代表宿主环境。JSON 的 `runtime_collection_mode` 为 `unverified`，需按新会话的 Trace ID 验证。
- 本机安装记录、产物和原生 hook/plugin 登记。Rust hook 还会执行无采集行为的 `--version`，无法运行时检查失败并提示运行库或架构问题。DSH 使用安装成功时的原生配置摘要，配置后来变化则显示 `unverified`。
- 已保存凭据能否通过现有 ingest-token 接口换取当前 Agent 的短期 token；不发送模拟遥测，也不写缓存。HTTP 401/403 标为无效，网络失败标为未验证。
- 配置查询 Key 后，读取当前空间、当前 Agent 最近一小时真实 trace；没有 Key 显示“上报数据未验证”。结果说明该空间下该 Agent 有数据，不证明来自本机。`last_active_at` 不参与判定。

本机安装、登记或凭据未通过/未验证时状态命令返回非零。没有 Key 或最近一小时没有 trace 是独立的数据检查结果，不等同于安装失败。状态检查需要访问部署的安装器地址。

## 部署前提

- 客户端支持 Linux/macOS × x64/arm64。Linux GNU hook 的发布基线为 glibc 2.28（覆盖 glibc 2.32）；CI 在 manylinux_2_28 中构建并执行产物，同时检查 ELF 所需 glibc 版本。Alpine/musl 需要部署另行提供对应产物。
- 安装 Rust hook 时，下载与 SHA-256 校验后会先执行 `--version`；兼容性检查通过才修改原生配置和确认安装完成。旧部署仍需重新发布插件及后端镜像，再运行 `aiv connect <agent>` 才能取得修复后的产物。
- Python 3.11+；宿主 Agent 已安装并完成自己的模型账号配置。Node 插件还需要 Node.js。AI Vision 登录不替代宿主 Agent 的登录。
- 后端 `PUBLIC_REGISTRATION_ENABLED=true` 才允许注册；关闭时只能使用已存在的账号登录。
- `--endpoint` 必须能从客户端访问，并路由 `/api` 与 `/openapi`。后台安装授权返回的 `base_url` 也必须可达，不能是仅容器内部可解析的地址。
- `AIVISION_OTLP_INGEST_ENDPOINT` 必须是宿主 Agent 可达的网关地址；平台地址可达不代表网关可达。远程机器不能使用部署机的 `127.0.0.1`。
- 生产使用 HTTPS 与安全 Cookie。HTTP 本地开发需要服务端允许非 Secure Cookie；不能用客户端绕过。
- 后端镜像需包含新版 `scripts/connect` 和目标平台的 `dist/` 产物，SigNoz datasource 与网关需完成既有初始化。没有远端镜像时可本地构建同一个镜像；客户端安装流程不变，但本地仅构建一个架构的 hook 不等于具备四平台产物。

团队成员邀请、Windows、hook 自动升级和卸载不在本轮范围内。
