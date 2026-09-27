# 业务应用区

`applications/<id>/` 保存业务源码，Bifrost 门户与网关代码留在仓库根目录。现有目录：

| 目录 | 入口 | 业务数据（Git 忽略） |
| --- | --- | --- |
| `ticket/` | `python -m web` | `ticket/.runtime_web/`、`ticket/.runtime/` |
| `douyin/` | `python -m web` | `douyin/datas/` |

每个应用继续维护自己的页面、API、测试和业务数据结构。Bifrost 不读取业务凭证，也不拆分业务数据。现有两个应用是从同级旧仓库复制的快照，包含 Ticket 尚未提交的 `web/` 工作台及其余工作区改动；旧仓库未删除。运行数据已复制到上述目录，源码与数据的后续改动应以此处为准。迁移时不要把数据库、凭证密钥或会话数据加入 Git。

其他团队用 Go、Python 或其他语言接入时：

1. 将源码和 `app.yaml` 放在 `applications/<id>/`。YAML 的 `id` 与目录名一致，用参数数组填写 `build`（可选）和 `start`。参考 [Go 示例](../examples/weixin-go/app.yaml)。
2. 启动参数用 `${PORT}` 接收平台分配的端口，应用只监听 `127.0.0.1`。业务页面、API、静态资源照常使用自己的根路径，无需适配平台前缀。
3. 管理员打开门户管理页即可发现应用；点击启动时平台构建并运行。无需改 `apps.py` 或 Caddyfile。
4. 有持久数据时可在启动参数中使用 `${DATA_DIR}`，它指向应用的 `.runtime/`；构建产物放 `.bifrost/`。有后台任务时自行实现安全退出，必要时提供可选状态接口。

启动与关闭由 Bifrost 在本机控制进程，业务服务不暴露启停 HTTP 接口。
