# 应用接入协议

开发者正常开发自己的 HTTP 应用，把代码和一份 `app.yaml` 放进 `applications/<id>/`。Bifrost 不加载业务代码，也不要求接入 SDK。每个应用是独立进程，有自己的内部监听端口和外部访问端口；门户只管理账号、授权和启停。

## 最小例子：Go 应用

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
build: [go, build, -o, .bifrost/bin/weixin, .]
start: [.bifrost/bin/weixin, --port, "${PORT}"]
```

`build` 在应用目录执行，生成的文件路径由开发者决定；`start` 运行该文件。`.bifrost/bin/weixin` 只是示例文件名，不是规定。已经交付可执行文件时可省略 `build`。两项命令都是参数数组，平台不会经过 shell。`start` 的第一个参数是相对于应用目录的可执行文件路径，也可由受信任的部署者指定绝对路径或应用目录外的解释器。Go 示例源码见 [examples/weixin-go](examples/weixin-go)。

## 必须遵守的约定

| 约定 | 开发者需要做什么 |
| --- | --- |
| 配置 | `schema: 1`、与目录同名的 `id`、`name` 和 `start` 必填；`build` 可选。 |
| 端口 | `start` 参数中包含 `${PORT}`；程序使用收到的端口监听 `127.0.0.1`。参数名由程序自己定，例如 `--port`。 |
| HTTP | 程序正常提供 `/`、`/api/...`、`/static/...` 等路径；无需平台前缀。 |
| 退出 | 收到 `SIGTERM` 后完成或保存必要工作并退出。 |

这就是最小协议。应用无需实现登录鉴权接口、启停接口或平台 SDK。平台在每次转发请求前检查门户登录与应用授权；应用不会收到门户的 `portal_session` Cookie。同一应用的获授权用户共用一个业务进程和它的数据。若业务需要区分具体用户，须在业务层另行设计身份机制；当前平台不向应用传递可信用户身份。

## 可选配置

| 字段 | 用途 |
| --- | --- |
| `description` | 门户显示的说明。 |
| `data_dir` | 应用目录内的持久数据目录，默认为 `.runtime`；启动参数可使用 `${DATA_DIR}`。 |
| `ready_path` | 就绪检查 URL，例如 `/.well-known/bifrost/ready`，返回 `{"ready": true}`。没有后台初始化时可不填。 |
| `activity_path` | 关闭前的忙闲检查 URL，例如 `/.well-known/bifrost/activity`，返回 `{"idle": true}`。有在途任务时返回 `{"idle": false, "reason": "正在处理"}`；无法读取时平台拒绝关闭。 |
| `internal_port` | 保留已有服务的固定内部端口时使用；新应用通常省略，让平台分配。 |

状态接口只供本机访问，不应返回凭证。若填写了路径，超时、非 200、格式错误或不符合预期都会使启动或关闭检查失败。`DATA_DIR` 是业务数据，不应作为可清理的构建缓存；构建产物可放在 Git 忽略的 `.bifrost/`。

## 接入与运行顺序

1. 部署者把受信任的应用目录放入 `applications/`。门户启动或管理员打开管理页时扫描 `app.yaml`；配置错误会显示在管理页，不执行应用代码。
2. Bifrost 为应用保存内部端口和独立外部端口，在 `.runtime/apps-ports.json` 中保持稳定。新应用默认从内部 `10000–10999`、外部 `9000–9999` 分配。
3. 管理员分配权限并点击“启动服务”。平台按 YAML 构建、启动独立进程，再检查进程、端口和可选就绪接口。发现应用不会自动授权或启动。
4. Caddy 根据生成的路由，把应用外部端口的请求转给内部端口；每次先询问门户是否允许当前用户访问。未登录的页面请求跳回门户登录，登录后返回应用地址。
5. 管理员点击关闭时，平台先检查可选的忙闲接口，再确认进程归属，最后发送 `SIGTERM`。无法确认时拒绝关闭。

例如门户在 `http://127.0.0.1:8080/`，Weixin 获得外部端口 `9000`、内部端口 `10000`：浏览器访问 `http://127.0.0.1:9000/`，Caddy 验权后转给 `127.0.0.1:10000`。开发者在项目外仍可单独用其他端口运行同一程序。

Ticket 和 Douyin 也使用相同的 YAML 接入方式；它们的 `internal_port` 和 `data_dir` 保留了迁移前的值。Douyin 另有一个仅用于官方二次验证的受限入口，该业务特例不改变普通应用协议。

## 可选：让平台打包应用

应用照常通过 `app.yaml` 接入。需要管理员在网页上生成安装包时，再添加 `package.yaml`。例如 [Weixin 示例](applications/weixin/package.yaml)：

```yaml
schema: 1
targets:
  windows-x64:
    artifact: .bifrost/packages/windows-x64/weixin.exe
    build: [python3, package_build.py, windows-x64, "${OUTPUT}"]
  macos-arm64:
    artifact: .bifrost/packages/macos-arm64/weixin.app.zip
    build: [python3, package_build.py, macos-arm64, "${OUTPUT}"]
```

`artifact` 必须在应用自己的 `.bifrost/packages/` 下。`build` 是在应用目录运行的参数数组，`${OUTPUT}` 会替换成产物的绝对路径，`${PYTHON}` 会替换成运行门户的 Python。需要指定构建机器时可写 `build_host: windows-x64` 或 `build_host: macos-arm64`。平台发现声明的预制安装包时可直接提供下载；有构建命令的目标会记录源码摘要，源码或打包配方变化后重新构建。打包任务只由管理员发起，不会启停正在运行的业务服务，构建日志放在门户 `.runtime/packages/` 下。构建命令与应用源码同样属于受信任的部署内容。

网页支持的目标名称是 `windows-x64`、`macos-arm64`、`macos-x64`、`android-arm64`、`ios-arm64`；各目标需要自己的构建配方和工具链。Windows 可执行文件可用 `.exe`，macOS `.app` 目录需压成 `.zip` 供下载，Android 使用 `.apk`，iOS 使用 `.ipa`。这份配置只描述如何构建已有的目标端程序，不能自动把普通 Python HTTP 服务转成原生手机程序。代码签名、系统权限和第三方依赖的发布要求仍由分发者处理。

Ticket 和 Douyin 的 Python 桌面配方见各自的 `package.yaml`。平台构建时为它们创建独立的 Python 3.12 环境，用 PyInstaller 收集依赖、静态页面和业务资源，再生成可独立启动的 macOS `.app.zip`。此打包应用在使用者电脑上启动本地 HTTP 服务并打开浏览器，数据存放在用户目录；它不经过 Bifrost 门户鉴权，因此只应交给有权使用该业务的人。
