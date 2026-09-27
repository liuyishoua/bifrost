# Bifrost

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-orange)](LICENSE)

Bifrost 是业务应用外侧的统一入口。Caddy 是唯一对外网关；Flask 门户负责账号、权限和服务启停。现有 Ticket 与 Douyin 源码位于 `applications/`，运行数据也迁入各自目录并被 Git 忽略。同一应用的获授权用户共用业务实例及数据。

## 使用许可

**本项目仅供学习、研究、测试及其他非商业用途，不得用于商业目的。** 对本仓库中版权所有者拥有权利的代码，适用 [PolyForm Noncommercial License 1.0.0](LICENSE)；非商业用途下可依该许可证使用、修改和分享。商业使用须事先取得版权所有者的单独授权。

第三方代码、素材和依赖仍受其各自许可证约束，票务应用的依赖说明见 [THIRD_PARTY_NOTICES.md](applications/ticket/THIRD_PARTY_NOTICES.md)。本仓库先前以 MIT 许可证发布的历史版本不受本次许可变更追溯限制。本项目属于**源码可见、限制商用**，不应标为 MIT 或 OSI 意义上的开源许可证。

## 接入契约

新应用只需把源码和 `app.yaml` 放入 `applications/<id>/`。平台自动发现配置，管理员点击启动后，平台按配置构建、运行并生成 Caddy 路由。接入方无需修改 `apps.py`、门户或 Caddyfile。完整说明见 [INTEGRATION.md](INTEGRATION.md)，可运行示例见 [examples/weixin-go](examples/weixin-go)。

1. 只监听 `127.0.0.1`；配置中的 `${PORT}` 由平台替换成分配的端口。
2. 页面、静态资源和 API 照常使用 `/`、`/static`、`/api` 等路径；新应用拥有独立访问端口。
3. 收到 `SIGTERM` 时完成必要的业务清理。有后台任务的应用可选择提供活动状态接口，以阻止任务进行时被关闭。

通用进程控制在 `service_control.py`，自动发现与校验在 `app_registry.py`，路由生成在 `gateway_routes.py`。新应用默认不需要 Bifrost 专用接口；现有 Ticket、Douyin 保留原有状态适配器。进程归属不明时，门户拒绝关闭。门户进程必须有权检查本机进程，且与业务服务使用同一本机用户运行。

**应用放置方式：**业务源码与 `app.yaml` 放在 `applications/<应用ID>/`；构建产物放 `.bifrost/`，运行数据放 `.runtime/`，两者均被 Git 忽略。详细目录约定见 [applications/README.md](applications/README.md)。

## 本机启动

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3.12 -m venv .venv-ticket
.venv-ticket/bin/pip install -r applications/ticket/requirements.txt
python3.12 -m venv .venv-douyin
.venv-douyin/bin/pip install -r applications/douyin/requirements-web.txt
mkdir -p .runtime
chmod 700 .runtime
printf '%s\n' '请换成至少十位的管理员密码' > .runtime/admin-password
chmod 600 .runtime/admin-password
.venv/bin/python portal.py bootstrap-admin admin --password-file .runtime/admin-password
.venv/bin/python portal.py
# 另一个终端
caddy run --config Caddyfile
```

门户监听 `127.0.0.1:8790`，Caddy 本机入口为 `http://127.0.0.1:8080`，抖音二次验证入口为 `http://127.0.0.1:8081`。新应用由平台分配 `9000–9999` 中的对外端口；需要动态重载路由时，让门户进程设置 `BIFROST_CADDY_CONFIG` 为正在使用的 Caddyfile 绝对路径。先启动门户，再启动 Caddy。两个旧业务应用各用独立 Python 3.12 环境。运行数据目录 `.runtime/` 含门户 SQLite、业务日志与生成的路由；业务数据分别在 `applications/ticket/.runtime_web/` 和 `applications/douyin/datas/web/`，需备份，不能作为缓存清理。迁移来源仓库保留为原始副本，确认业务无误后再自行归档。

## 公网部署

使用 `Caddyfile.public`，设置 `BIFROST_PUBLIC_IP`、`BIFROST_TLS_CERT`、`BIFROST_TLS_KEY`。证书须在 SAN 中包含该 IP。门户进程另设 `BIFROST_PUBLIC_ORIGIN=https://公网IP`、`BIFROST_VERIFY_ORIGIN=https://公网IP:8443` 与 `BIFROST_CADDY_CONFIG=/绝对路径/Caddyfile.public`。防火墙开放 443、8443 和已分配的应用端口；8790、8767、8766 与新应用内部端口仅绑定回环。

抖音现有版本已经在 HTML 中按 `X-Forwarded-Prefix` 注入 `<base>`，前端 `mount.mjs` 使用前缀构造页面与 API 路径；票务页面使用相对资源与 API 路径。更换业务版本时，先核对这些能力和二次验证来源，再在业务服务停止窗口应用该版本对应的路径适配改动。不要直接对未知版本应用补丁。

## 管理和安全

自行注册的账号为待审批。管理员在后台启用账号并分配应用，授权在每次网关请求时检查。禁用、重置密码撤销旧会话；撤权后的下一个请求即被拒绝。管理员重置时输入临时密码，不读取原密码；用户首次登录须先改密。登录最多 12 小时，可选择保持 30 天。门户表单使用 CSRF 令牌和来源检查，应用写请求由内部鉴权再次核对来源。

网关拒绝外部 `/internal/*`，每个应用请求先 `forward_auth` 再转发。新应用以独立端口转发，旧应用继续使用固定前缀。网关清除客户端提交的转发/身份提示头，并从上游请求中移除门户会话 Cookie。门户故障时鉴权失败，Caddy 不继续转发。二次验证端口只开放验证专用路径。网关设计依据 [Caddy forward_auth](https://caddyserver.com/docs/caddyfile/directives/forward_auth) 和 [Caddy 路由说明](https://caddyserver.com/docs/caddyfile/directives/handle)。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

门户前端已有注册、登录、应用首页、管理后台、个人设置和操作记录页面。自动测试覆盖账号、授权、服务状态及来源校验；本机网关联调验证了 Ticket 首页、脚本、只读 API 和抖音二次验证专用路径。扫码、官方二次验证完成及消息发送仍需在真实账号下单独联调。
