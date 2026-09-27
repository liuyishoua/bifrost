# Bifrost 应用接入

接入方交付正常开发的应用代码，以及说明如何构建和启动的 `app.yaml`。Bifrost 扫描 `applications/<id>/`，管理员在门户中分配权限并启停应用。平台负责端口、路由、构建、进程管理和逐请求鉴权。

## 最小配置

```text
applications/weixin/
  app.yaml
  go.mod
  main.go
```

```yaml
schema: 1
id: weixin
name: 微信应用
description: 微信业务工作台
build: [go, build, -o, .bifrost/bin/weixin, .]
start: [.bifrost/bin/weixin, --port, "${PORT}"]
```

`schema` 固定为 `1`；`id` 必须与目录名一致；`name` 和 `start` 必填；`description`、`build` 可省略。`build` 和 `start` 都是参数数组，不经过 shell。启动文件必须位于应用目录内。平台将 `${PORT}` 替换为分配的内部端口，将可选的 `${DATA_DIR}` 替换为该应用的 `.runtime/` 目录。构建工具须已在部署机器安装。

应用只监听 `127.0.0.1`，并能在收到 `SIGTERM` 时退出。页面、API 和静态文件可以照常使用 `/`、`/api`、`/static` 等路径。平台给每个新应用分配独立访问端口，例如 `https://公网IP:9000/`，接入方不需要处理平台前缀或修改页面链接。完整 Go 示例在 [examples/weixin-go](examples/weixin-go)。

## 平台如何接入

1. 部署者把受信任的应用目录放进 `applications/`。门户启动或管理员打开管理页时扫描 YAML；错误会显示在管理页，不执行任何应用代码。
2. 管理员给用户分配应用权限。发现应用本身不会自动授权或启动。
3. 管理员在应用首页点击启动，平台执行 `build`，随后执行 `start`，检查进程及监听端口。点击关闭时，平台确认进程归属后发送 `SIGTERM`。
4. 门户生成 `.runtime/apps.local.caddy` 与 `.runtime/apps.public.caddy`。Caddyfile 导入这些文件；设置 `BIFROST_CADDY_CONFIG` 后，管理页刷新可校验并重新加载路由。先启动门户，再启动 Caddy。

新应用内部端口在 `10000–10999` 分配，对外端口在 `9000–9999` 分配，并记录到 `.runtime/apps-ports.json`。公网部署须开放已分配的对外端口，证书须覆盖公网 IP。业务监听端口只在回环地址开放。平台会为每个请求检查登录和应用授权；业务服务收不到门户会话 Cookie。

## 可选业务状态接口

一般应用不需要专门的 Bifrost 接口。若端口已监听但业务尚未准备好，可在 YAML 填 `ready_path: /health`，让该路径返回 `{"ready": true}`。若有后台发送、同步等任务，可填 `activity_path: /activity`，让该路径返回 `{"idle": true}`；非空闲或读取失败时平台拒绝关闭。接口只允许本机访问，不应返回业务凭证。应用仍负责在 `SIGTERM` 时完成或保存自己的工作。

Ticket 与 Douyin 暂时沿用旧的固定路由、启动配置和状态适配器，以保持现有行为。它们的兼容说明在 [integrations/](integrations/)。
