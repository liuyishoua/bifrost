# 兼容适配器

新应用放在 `applications/<id>/`，附 `app.yaml` 即可由平台发现，通常不需要修改 `integrations/`。接入说明见 [INTEGRATION.md](../INTEGRATION.md)。

这里保留 Ticket、Douyin 的旧版状态适配器，以及供它们参考的接入材料。现有两项业务仍使用固定进程参数和固定前缀路由；迁移它们的配置须另行验证。新应用若需要更严格的就绪或安全退出判断，可先使用 YAML 的可选 `ready_path`、`activity_path`。
