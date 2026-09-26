# 业务应用区

`applications/<id>/` 保存业务源码，Bifrost 门户与网关代码留在仓库根目录。现有目录：

| 目录 | 入口 | 业务数据（Git 忽略） |
| --- | --- | --- |
| `ticket/` | `python -m web` | `ticket/.runtime_web/`、`ticket/.runtime/` |
| `douyin/` | `python -m web` | `douyin/datas/` |

每个应用继续维护自己的页面、API、测试和业务数据结构。Bifrost 不读取业务凭证，也不拆分业务数据。现有两个应用是从同级旧仓库复制的快照，包含 Ticket 尚未提交的 `web/` 工作台及其余工作区改动；旧仓库未删除。运行数据已复制到上述目录，源码与数据的后续改动应以此处为准。迁移时不要把数据库、凭证密钥或会话数据加入 Git。

其他团队用 Go、Python 或其他语言接入时：

1. 在 `applications/<id>/` 放源码和可复现的构建说明，可执行文件由本机构建并加入 `.gitignore`。
2. 应用只监听 `127.0.0.1`，实现 [接入协议](../INTEGRATION.md)中的只读 `ready` 与 `activity` 接口，以及正常退出信号处理。
3. 将业务运行数据放在受保护目录，并为其加 `.gitignore`；在 `apps.py` 注册固定启动参数和数据目录。
4. 在 `Caddyfile` 与 `Caddyfile.public` 配置固定前缀路由；在 `integrations/<id>/APP.md` 记录所有者、就绪和安全退出检查。

启动与关闭由 Bifrost 在本机控制进程，业务服务不暴露启停 HTTP 接口。
