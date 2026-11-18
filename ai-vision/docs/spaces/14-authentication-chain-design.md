# 认证与鉴权链路

## 网页账号

账号使用邮箱注册、密码登录。`User.uuid` 是稳定的用户标识，`normalized_email` 用于登录查找；显示名称不参与权限判断。

1. `GET /api/v1/auth/csrf` 获取 CSRF Cookie。
2. `POST /api/v1/auth/register` 提交 `name`、`email`、`password`、`password_confirm`，或通过 `POST /api/v1/auth/login` 登录已有账号。
3. 浏览器使用 HttpOnly Cookie 保存访问和刷新凭据。修改请求携带 `X-CSRFToken`。
4. `LocalSessionAuthentication` 验证 JWT、签发方、数据库中的有效会话及账号状态，再由空间权限层检查成员角色。
5. `POST /api/v1/auth/refresh` 轮换刷新凭据；退出通过 `POST /api/v1/auth/logout` 撤销会话。

认证实现见 [authentication.py](../../aivision-py-be/apps/core/authentication.py)，账号入口见 [local_auth.py](../../aivision-py-be/apps/core/views/local_auth.py)。

## 程序访问

`UnifiedExternalAuthentication` 接受空间级 Bearer SpaceKey 或观测凭证 Basic 认证。凭证只授权所属空间，不能替代网页会话，也不能由请求参数覆盖服务端确认的空间身份。

Langfuse 项目绑定、凭证生成与轮换保持独立，参见 [观测 API 与认证](02-space-observation-api-and-auth-design.md)。

## Agent 安装与采集

网页根据已登录账号和空间权限生成一次性安装授权；安装器兑换仅供当前 Agent 使用的长期凭据，并自动续期短期 OTLP 采集令牌。网关验证令牌，写入可信空间、用户和 Agent 标识。

采集字段中的 `ant.username` 等名称是已有数据协议的一部分，标识值不要求纯数字或固定长度。客户端不能通过修改这些字段跨空间写入。

## 部署配置

通过 `AIVISION_ENV` 选择环境文件，通过 `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、`CORS_ALLOWED_ORIGINS` 配置部署域名。账号与空间初始化步骤见 [CLI 注册与接入](../../../aivision-cli/docs/auth.md#从新账号开始)。
