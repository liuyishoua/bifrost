# Bifrost 应用接入协议 v1

Bifrost 把“账号与授权”和“业务运行”分开。接入应用可以由任何语言编写，只要它提供 HTTP 服务和下面两个只读状态接口。门户不需要知道应用的页面、任务模型和数据结构。

## 目录与职责

| 文件 | 职责 |
| --- | --- |
| `apps.py` | 固定应用清单：ID、入口、仓库、数据目录、端口、启动参数 |
| `Caddyfile` / `Caddyfile.public` | 固定外部路由、逐请求鉴权、去前缀转发 |
| `portal.py` | 账号、会话、授权、审批与审计 |
| `service_control.py` | 通用本机进程启动、归属核验、正常退出 |
| `integrations/base.py` | 应用接入协议及通用 HTTP 适配器 |
| `applications/ticket/` / `douyin/` | 两个现有业务应用的源码及各自受保护的数据目录 |
| `integrations/ticket/` / `douyin/` | 现有应用的状态翻译 |
| `integrations/_template/APP.md` | 其他团队可复制的接入卡 |

## 对新应用的 HTTP 契约

服务必须只监听 `127.0.0.1`，并提供：

```http
GET /.well-known/bifrost/ready
200 Content-Type: application/json
{"ready": true}
```

只有页面/API 所需组件真正可用时才返回 `true`。错误、超时、非对象 JSON 或 `ready` 不为布尔真，都视为未就绪。

```http
GET /.well-known/bifrost/activity
200 Content-Type: application/json
{"idle": true, "reason": ""}
```

`idle` 仅在可以安全正常退出、没有在途写入或后台工作时为 `true`。正在抢票、发送、搜索、扫码等情况应返回 `{"idle": false, "reason": "简短原因"}`。读取失败、字段缺失或超时一律拒绝关闭。接口不得触发业务操作，也不得返回凭证或用户数据。服务继续负责自己已提交任务的管理；Bifrost 只在关闭前读取状态。

## 页面与代理契约

- 应用在固定前缀下可用，例如 `/newapp/`。资源、导航、API 和深层刷新都必须留在该前缀。可以接收可信 `X-Forwarded-Prefix` 生成链接，或通过相对 URL / 基路径实现。
- Caddy 对每个页面、资源和 API 请求先调用门户 `/internal/auth/{id}`，获准后去掉前缀。原方法、查询和请求体保持不变；门户 Cookie 不发送给上游。
- 写请求必须来自门户同源。公网使用 HTTPS。业务服务不可对公网监听，否则绕过门户权限。
- 如果应用需要额外来源，如抖音二次验证，需在网关显式列出仅允许的路径及其授权规则。

## 增加应用的步骤

1. 将业务应用放在 `applications/<id>/`；复制 `integrations/_template/APP.md` 到 `integrations/<id>/APP.md` 并填写接入信息。在 `apps.py` 增加固定 `App` 记录，启动命令的首项使用解释器或可执行文件的绝对路径，整条命令用参数数组，不接受网页输入。数据目录放在应用内的受保护目录并加入 `.gitignore`，在启动参数中明确数据目录供进程归属核验。
2. 在两份 Caddyfile 中增加固定 `/newapp/*` 路由，先 `forward_auth`，再 `uri strip_prefix`、`reverse_proxy`；清理客户端伪造转发头并移除上游 Cookie。
3. 默认使用 `integrations/base.py` 的 `HttpContractAdapter`。无法提供 v1 HTTP 状态接口的已有应用，可新增一个只读适配器并登记在 `integrations/__init__.py`。
4. 用未登录、待审批、未授权、获授权、撤权和门户故障的请求验证路由；用页面渲染、资源加载、深层刷新和只读数据检查业务接入。

**启停协议：**应用不提供 `start` 或 `stop` HTTP 接口。已停止的应用无法响应启动请求。Bifrost 根据服务端固定命令启动进程；关闭前核验端口和进程归属，读取 `activity`，随后发送 `SIGTERM` 并等待退出。应用应处理正常退出信号，等待自身在途资源释放。若应用由 systemd 或容器运行，应新增服务端固定的进程管理适配器，不让网页提供命令。

**Go 应用示例：**源码放在 `applications/mygo/`，本机编译的可执行文件放在该目录的 Git 忽略路径。在 `apps.py` 中将 `command` 配成绝对可执行文件路径及固定参数数组，例如 `(<绝对路径>/mygo, "--listen", "127.0.0.1:8768", "--data-dir", <绝对数据目录>)`。Go HTTP 服务实现上述两个 JSON 接口，页面和 API 使用 `/mygo/` 基路径或可信 `X-Forwarded-Prefix`，收到 `SIGTERM` 后停止接收新工作并正常退出。Bifrost 无需 Go SDK，也无需应用调用门户的启停 API。

这套协议不把应用注册变成动态反向代理，也不让应用获得门户账号数据库或修改权限。新增语言只影响业务服务实现，不影响 Bifrost 账号流程。
