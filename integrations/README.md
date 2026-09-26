# 应用接入区

这里存放 **Bifrost 侧的接入材料**；业务源码及数据在 `applications/`。

```text
bifrost/
  apps.py                 固定应用清单
  Caddyfile*              固定网关路由
  applications/           各语言业务应用源码及受保护的数据目录
  integrations/
    base.py               所有语言共用的 HTTP 状态协议
    ticket/               票务旧版兼容适配器
    douyin/               抖音旧版兼容适配器
    _template/            新应用接入模板
```

新应用放在 `applications/<id>/`。如果实现 [标准 HTTP 接口](../INTEGRATION.md)，只需按 `_template/APP.md` 填写接入信息，再由平台维护者将固定配置加入 `apps.py` 和两份 Caddyfile；不需要写 Python 适配器。已有应用无法改动时，在 `integrations/<id>/` 放只读状态翻译代码，并登记到 `integrations/__init__.py`。固定配置要经过代码审查，不接受网页动态添加上游或启动命令。
