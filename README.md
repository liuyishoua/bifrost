# Bifrost

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-orange)](LICENSE)

Bifrost 是业务应用外侧的统一入口。Caddy 是唯一对外网关；Flask 门户负责账号、权限和服务启停。现有 Ticket 与 Douyin 源码位于 `applications/`，运行数据也迁入各自目录并被 Git 忽略。同一应用的获授权用户共用业务实例及数据。

## 使用许可

**本项目仅供学习、研究、测试及其他非商业用途，不得用于商业目的。** 对本仓库中版权所有者拥有权利的代码，适用 [PolyForm Noncommercial License 1.0.0](LICENSE)；非商业用途下可依该许可证使用、修改和分享。商业使用须事先取得版权所有者的单独授权。

第三方代码、素材和依赖仍受其各自许可证约束，票务应用的依赖说明见 [THIRD_PARTY_NOTICES.md](applications/ticket/THIRD_PARTY_NOTICES.md)。本仓库先前以 MIT 许可证发布的历史版本不受本次许可变更追溯限制。本项目属于**源码可见、限制商用**，不应标为 MIT 或 OSI 意义上的开源许可证。

## 接入契约

`apps.py` 是服务端应用清单，包含固定 ID、名称、入口、仓库、数据目录、端口、就绪接口及启动参数。新增应用需同时增加 Caddy 固定路由。完整的语言无关 HTTP 契约见 [INTEGRATION.md](INTEGRATION.md)，给其他团队填写的接入卡见 [integrations/_template/APP.md](integrations/_template/APP.md)。门户账号模块不依赖业务语言；业务服务只需满足以下适配契约：

1. 仅监听回环地址，能在固定 URL 前缀下正确生成静态资源、页面导航和 API 路径。
2. 提供只读就绪响应，以及足以判断后台任务、扫码、搜索和发送是否在途的只读状态。
3. 能以正常退出信号停止，不因门户退出而停止。

通用进程控制在 `service_control.py`，标准 HTTP 适配器在 `integrations/base.py`。现有两个应用由独立适配器翻译状态；新服务实现标准接口后无需新增 Python 适配器。进程归属不明、活动状态不可读时，门户拒绝关闭。门户进程必须有权检查本机进程，且与业务服务使用同一本机用户运行。

**应用放置方式：**业务源码放在 `applications/<应用ID>/`，语言和框架不限；运行数据放在该应用的受保护目录并加入 `.gitignore`。`integrations/<应用ID>/` 只放接入资料和必要的兼容适配器。复制 `_template/APP.md` 填好信息，再由平台维护者添加 `apps.py` 和两份 Caddyfile 的固定配置。标准接口已实现时，不需要写适配器。详细目录约定见 [applications/README.md](applications/README.md)。

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

门户监听 `127.0.0.1:8790`，Caddy 本机入口为 `http://127.0.0.1:8080`，抖音二次验证入口为 `http://127.0.0.1:8081`。两个业务应用各用独立 Python 3.12 环境。运行数据目录 `.runtime/` 含门户 SQLite、业务日志；业务数据分别在 `applications/ticket/.runtime_web/` 和 `applications/douyin/datas/web/`，需备份，不能作为缓存清理。迁移来源仓库保留为原始副本，确认业务无误后再自行归档。

## 公网部署

使用 `Caddyfile.public`，设置 `BIFROST_PUBLIC_IP`、`BIFROST_TLS_CERT`、`BIFROST_TLS_KEY`。证书须在 SAN 中包含该 IP。门户进程另设 `BIFROST_PUBLIC_ORIGIN=https://公网IP` 和 `BIFROST_VERIFY_ORIGIN=https://公网IP:8443`，这样 Cookie 带 `Secure`，抖音页面也会生成正确的验证链接。防火墙只开放 443、8443；8790、8767、8766 仍仅绑定回环。

抖音现有版本已经在 HTML 中按 `X-Forwarded-Prefix` 注入 `<base>`，前端 `mount.mjs` 使用前缀构造页面与 API 路径；票务页面使用相对资源与 API 路径。更换业务版本时，先核对这些能力和二次验证来源，再在业务服务停止窗口应用该版本对应的路径适配改动。不要直接对未知版本应用补丁。

## 管理和安全

自行注册的账号为待审批。管理员在后台启用账号并分配应用，授权在每次网关请求时检查。禁用、重置密码撤销旧会话；撤权后的下一个请求即被拒绝。管理员重置时输入临时密码，不读取原密码；用户首次登录须先改密。登录最多 12 小时，可选择保持 30 天。门户表单使用 CSRF 令牌和来源检查，应用写请求由内部鉴权再次核对来源。

网关拒绝外部 `/internal/*`，每个应用请求先 `forward_auth`，成功后才去前缀并转发。网关会清除客户端提交的转发/身份提示头，并且不把门户 Cookie 转给业务服务。门户故障时鉴权失败，Caddy 不继续转发。二次验证端口只开放验证专用路径。网关设计依据 [Caddy forward_auth](https://caddyserver.com/docs/caddyfile/directives/forward_auth) 和 [Caddy 路由说明](https://caddyserver.com/docs/caddyfile/directives/handle)。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

门户前端已有注册、登录、应用首页、管理后台、个人设置和操作记录页面。自动测试覆盖账号、授权、服务状态及来源校验；本机网关联调验证了 Ticket 首页、脚本、只读 API 和抖音二次验证专用路径。扫码、官方二次验证完成及消息发送仍需在真实账号下单独联调。
