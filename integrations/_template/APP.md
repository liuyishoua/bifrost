# 新应用接入卡（复制到 `integrations/<应用ID>/APP.md`）

| 项目 | 填写值 |
| --- | --- |
| 应用 ID | `your_app`（固定、小写） |
| 展示名称与说明 |  |
| 应用源码目录 `applications/<id>/` |  |
| 应用数据目录（Git 忽略） |  |
| 仅回环监听端口 |  |
| 外部入口 | `/your_app/` |
| 启动命令参数数组 |  |
| Python/Node/Go 等运行时与版本 |  |
| 首页、资源、深层页面、API 的检查路径 |  |
| 在途工作类型与安全退出条件 |  |
| 是否有独立验证来源及允许的路径 | 无 / 说明 |

应用应在自己的源码中实现以下只读接口，监听 `127.0.0.1`：

```http
GET /.well-known/bifrost/ready
200 {"ready":true}

GET /.well-known/bifrost/activity
200 {"idle":true,"reason":""}
```

`ready` 要反映真实可用状态；`idle` 要检查任务、发送、搜索、扫码等所有在途工作。检查失败应返回非 200 或 `idle:false`。应用正常退出时应处理 SIGTERM。业务页面所有资源、导航和 API 在 `/your_app/` 前缀下仍可工作。

平台接入步骤：

1. 将源码放进 `applications/<id>/`，在 `apps.py` 增加固定 `App` 记录，明确源码、数据目录、端口和绝对路径启动命令。
2. 在 `Caddyfile` 与 `Caddyfile.public` 各增加固定 `/your_app/*` 路由：清理伪造头 → `forward_auth` → 去前缀 → 上游转发，并移除 Cookie。
3. 标准接口已实现时直接使用 `HttpContractAdapter`；旧应用需要状态翻译时才在此目录写适配器。
4. 验证未登录和撤权后不可直连；授权后检查首页、静态资源、深层刷新、API、写请求来源和数据一致性。

应用业务代码和受保护数据留在 `applications/<id>/`；此接入卡只记录桥接信息，不放密码、密钥或业务数据。
