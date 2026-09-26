# AGENTS.md

## 项目定位

本仓库包含既有抖音 Python 接口与新增的本机 Web 控制台。`web/` 已实现搜索入库、用户管理、账号鉴权、预约任务、单任务执行大盘和全局总览；SQLite 自动建库。原有作品、直播、创作者脚本继续保留。

本文件用于帮助 AI 理解现有项目、定位代码和避免误操作。产品诉求统一维护在 [docs/requirements.md](docs/requirements.md)，不要继续把需求讨论堆进原 README，也不要把方案建议当作已实现功能。

网站与代码设计见 [docs/design.md](docs/design.md)：当前设计为搜索入库、用户管理、任务列表与详情、执行大盘、账号管理；创建任务后必须处于未开始状态，明确开始才执行。现已实现首版，实际启动和验证边界见 [docs/run.md](docs/run.md)，接口契约见 [docs/web-api.md](docs/web-api.md)。

既有接口理解来自源码检查；Web 逻辑已用临时数据库、受控替身和演示模式验证，不代表真实抖音账号联调成功。后续代码变化时同步更新；以当前源码和实际验证为准。

## 工作约定

- 先读用户指令及本地补充约定；如存在 `Codex.local.md`，一并遵循。当前初始化时未找到该文件。
- 修改前说明假设和验证目标；有影响实现的歧义需明确，不隐藏猜测。
- 只改任务必要代码，保持现有风格；不顺手重构签名、协议或无关模块。
- 优先复用已有接口，不为单次用途创建抽象，不提前加入复杂基础设施。
- 需求变更维护 `docs/requirements.md`；后台事实与运行说明维护本文件。
- 不覆盖用户已有工作，不读取或输出真实 Cookie、私钥、票据；脱敏样本用于接口分析。
- 如执行 pigsy_skill 脚本，相对路径需加 `/Users/didi/other-projec/pigsy_skill/` 前缀。

## 代码导航

| 文件 | 作用与接入点 |
| --- | --- |
| [dy_apis/douyin_api.py](dy_apis/douyin_api.py) | `DouyinAPI`：用户搜索、资料、创建会话、发送文本等核心接口 |
| [builder/auth.py](builder/auth.py) | `DouyinAuth`：账号 Cookie、证书、私钥、设备状态、HTTP 会话；`open`、`from_cookie`、`from_qrcode_login` |
| [dy_apis/login_api.py](dy_apis/login_api.py) | `DYLoginApi`：二维码生成与轮询、登录、凭证保存 |
| [utils/common_util.py](utils/common_util.py) | `load_env`、`get_auth`、`init`；现有入口包含全局 Auth，不能直接用作多账号隔离层 |
| [builder/proto.py](builder/proto.py) | 私信 Protobuf 请求组装，包括接收者 ID、会话票据、消息 ID |
| [builder/header.py](builder/header.py)、[builder/params.py](builder/params.py) | 请求头、参数和签名组装，网页层优先复用 |
| [utils/http_client.py](utils/http_client.py) | 基于 `curl_cffi` 的请求层 |
| [main.py](main.py) | 脚本示例入口，**当前有未注释的真实私信发送调用** |
| [utils/data_util.py](utils/data_util.py) | 作品整理、媒体下载、Excel 导出；不是用户管理数据库 |
| [dy_apis/douyin_recv_msg.py](dy_apis/douyin_recv_msg.py) | 私信 WebSocket 接收，首版仅发送流程不必接入 |

任务列表支持逐行编辑配置、开始、暂停、恢复和删除，复用既有弹窗及接口；绑定限定当前行，操作后保持列表筛选/分页，编辑会重新读取任务状态。列表请求版本防止旧轮询覆盖新结果。独立“关闭任务”语义尚待确认。

Web 入口是 `web/__main__.py`，HTTP 层是 `web/app.py`；业务在 `web/service.py`、`accounts.py`、`search.py`、`tasks.py`，通过 `web/douyin.py` 调用既有接口。前端位于 `web/static/`（不要和根目录 Protobuf static 混用），全局统计在 `web/overview.py`，路由 `/overview`。 总览在运行任务下方展示当前运行账号近 30 分钟错误率，按实际发送账号和 attempted_at 统计终态，失败包含超时与响应异常；分母为 sent+failed，无样本返回 null。`web/static/charts.mjs` 提供两张历史曲线和任务发送节奏的共同折线及悬停/键盘/触屏提示，轮询保留所选时间；统计和图表不改变发送调度。测试位于 `web/tests/`。直播、互动与创作者发布不在 Web 首版范围内。

标签管理 `/tags` 与话术管理 `/message-templates` 由 `web/library.py` 提供，关系存入 tags、user_tags、message_templates。两个入库入口先选标签，默认勾选“二手车商”，可取消；升级不回填旧用户，重复入库只追加标签。用户管理支持来源关键词、多标签任一/全部筛选及单人、批量标签维护。创建任务复制话术正文并允许修改，后续修改/删除话术不影响任务快照。需求与交互见 `docs/requirements.md`、`docs/tags-and-templates.md`，测试见 `web/tests/test_library.py`。

## 用户搜索与身份字段

现有调用链：

```python
# 调用关系示意；auth 是已准备好的账号鉴权对象。
page = DouyinAPI.search_user(auth, query="关键词", offset="0", num="25")
users = DouyinAPI.search_some_user(auth, query="关键词", num=50)
profile = DouyinAPI.get_user_info(auth, user_url="https://www.douyin.com/user/<sec_uid>")
```

- `search_user` 返回原始 JSON；`search_some_user` 按 `user_list`、`has_more` 翻页，返回列表，不负责保存。
- `get_user_info` 返回原始 JSON，用户资料位于 `user`。
- 当前本地搜索样本中的用户资料位于列表项 `user_info`，包含 `follower_count` 与 `total_favorited`，未包含用户级收藏数；转换逻辑仍需处理可选字段缺失。作品 `statistics.collect_count` 不能当成用户资料字段。
- 本地样本返回 23 人但下一页 `cursor=25`。Web 分页应优先使用接口游标并检测是否前进，不根据返回人数猜测下一页；原 `search_some_user` 采用固定 offset 步进，不能直接承担完整页面进度与选择入库流程。

身份字段必须区分：`uid` 是数字用户 ID，创建私信会话使用它；`sec_uid` 用于用户主页查询；`unique_id` 是展示用抖音号。前端和数据库中的 ID 建议存字符串，避免大整数精度丢失。不能拿抖音号直接当接收者数字 ID。

入库字段与筛选门槛见需求文档；底层搜索不负责持久化，Web 的 Searches 负责逐页去重、暂存与选择入库。Web 已移除累计收藏采集、展示与筛选，数值指标仅保留粉丝数和累计获赞。另支持蓝 V 认证筛选，以 enterprise_verify_reason 是否为空判断，缺失为未知；个人认证不当作蓝 V。

## 指定账号与发送链路

一个发送账号对应一个独立的 `DouyinAuth` 和 HTTP 会话。搜索、创建会话、发送时明确使用哪个账号，不要把账号 A 的 Cookie 与账号 B 的扫码凭证拼在一起。

1. 用 `DouyinAuth.from_cookie(...)` 或 `from_qrcode_login(...)` 建立账号会话；本 Demo 不涉及创作者发布，可显式传 `bootstrap_creator=False`。
2. 扫码入口支持 `on_qrcode` 回调，每次换码都会传入二维码 URL；网页接入时用 `show_qr=False`，后台执行轮询，页面展示二维码和状态。
   现有 `qrcode_login` 会新建 Auth，不是给已有 Cookie Auth 追加票据。接入“导入 Cookie 后扫码”的产品流程时，校验扫码 UID 与原账号一致，再保存新的完整会话；不得盲目混合凭证。
3. 调用 `DouyinAPI.create_conversation(auth, to_user_id)`，获得 `conversation_id`、`conversation_short_id`、会话 `ticket`。
4. 调用 `DouyinAPI.send_msg(auth, conversation_id, conversation_short_id, ticket, content)`；`send_text` 是其别名，文本可以直接传字符串。
5. Web Tasks 保存任务与接收者快照，后台线程调度，前端轮询同一事务快照。任务创建不调用发送，开始才激活预约；不确定结果作为终态记录，按间隔继续下一位，不再逐条人工核对，服务重启不自动重发。

鉴权不是只有一个 Cookie：

- 现有环境变量包括 `DY_COOKIES`、`DY_TICKET`、`DY_TS_SIGN`、`DY_CLIENT_CERT`、`DY_PRIVATE_KEY`、`DY_DTRAIT_BLOB` 等。
- 创建会话的签名使用 `auth.private_key`；仅能搜索成功不能证明私信鉴权齐全。
- 发送前会调用 `get_identity_security_token` 获取短期身份令牌，并在同一个 Auth 中缓存；它不是名为 `linsi` 的 Cookie。
- Auth 中的鉴权 `ticket` 与 `create_conversation` 返回的会话 `ticket` 不要混用。
- `DYLoginApi.save_credential` 会写根目录 `.env`，属于现有单账号脚本方式；多账号网页应按账号保存完整凭证，不能反复覆盖同一份全局配置。
- Cookie、私钥、票据仅由后端保存和使用，不回传列表接口、不写入前端本地存储或日志。

账号管理支持 `PATCH /api/accounts/<id>` 更新 Cookie：独立 Auth 校验同一 UID，失败保留原凭证，成功清除旧扫码凭证并要求重新扫码；已有账号、用户和任务历史保留。更新与任务、搜索及鉴权互斥。前端提供“仅更新 Cookie”“更新并重新扫码”，原有单独扫码入口继续保留。新增能力以临时数据库、替身和独立浏览器验证，不代表真实账号发送成功。

**当前成功判断的边界：** 旧调用默认仍按顶层 `message == 'OK'` 返回 bool；新增可选 `return_details=True` 保留响应和未知 Protobuf 字段标志，Web 对拒绝记失败并计入任务累计与连续阈值，两项都超过允许次数才暂停；不可解读结果也记失败，并按任务阈值决定暂停。已发送指平台接口确认，不表示已读或保证送达。已移除人工登记发送结果的能力。

发送诊断：`utils/send_diagnostics.py` 只提取字段编号、白名单数值、HTTP 状态和异常分类，不保存响应字符串、Cookie、票据或消息正文。任务将耗时、client_message_id 和诊断写入 `events` 的 `send_diagnostic` 事件，接收者说明区分未知字段、协议解析失败、网络超时和内部异常；服务重启保留具体原因。`wire_codes` 的键是协议字段路径，数值不等同于已验证的业务含义。旧记录没有原始响应，不能靠新增诊断追溯还原。代码更新须在服务重新加载后生效，验证仅使用模拟响应，不代表真实发送成功。

发送结果解析已独立补齐实际 wire 路径：顶层 3 为数值状态，6.100 为发送响应，内部 1/3/5/6 分别提取消息 ID、发送状态、校验码及 JSON 业务结果。旧 Response.proto 保留兼容，不用于发送业务状态判定。仅外层 OK、三个状态均为 0、业务码 0 且存在正数消息 ID 时记接口确认（不代表接收或已读）；仅业务结果字段缺失且 HTTP 200、外层 OK、三个状态均为 0、有消息 ID、无错误描述及 wire 解析异常时，按用户确认记成功并标注“未返回业务码”；其他缺失或解析异常仍记失败。4002、8101 按用户确认的业务口径记成功，原业务码保留；其余非零业务码记失败；历史样本明确的 7180 记陌生人消息限流，任务按累计与连续失败阈值的 AND 条件决定暂停；其他明确非零错误记拒绝。未知附加字段仅作诊断，不单独推翻完整成功凭据。字段名称参考火山 IM 类型说明 https://www.volcengine.com/docs/6348/293494?lang=zh ，wire 编号与 7180 提示来自本地历史样本，未宣称 8101 有官方成功含义。旧 uncertain 历史统一迁移为 failed，保留原脱敏诊断，不重发。

扫码诊断由 `web/login.py` 包装原登录流程，失败时仅展示阶段、最近数字错误码及白名单扫码状态，不暴露异常原文和票据。错误码 7 可能是底层将空响应折算的轮询错误，不可直接等同于平台明确限流。账号身份不一致仍拒绝覆盖原凭证。页面失败/中断时不再显示“正在获取二维码”。旧的通用扫码失败提示不能追溯还原具体异常。

## 开发环境与运行边界

- Python 3.10+；真实扫码辅助代码还需 Node.js。无需独立 SQLite 服务，无前端 npm 工程。
- Web 依赖：`python -m pip install -r requirements-web.txt`。该文件只包含本控制台依赖，避开原 requirements.txt 中 blackboxprotobuf 的版本冲突及无关媒体依赖。
- 演示启动：`.venv/bin/python -m web --demo`，打开 `http://127.0.0.1:8765`。明确使用模拟账号和模拟发送。
- 真实启动：`.venv/bin/python -m web`，页面导入账号后才发起校验；任务必须手动开始。
- 数据目录分别为 `datas/web-demo/`、`datas/web/`；数据库与 credential.key 一起保留，不提交、不回显凭证。
- 仅支持本机单进程；同一账号可服务多个任务且同一时刻仅一条发送，不同账号由独立线程并发。进行中的指定任务借调账号，使其退出共享池，暂停或结束后归还。原 Dockerfile 仍启动 main.py，并不是 Web 镜像。
- 不运行 `python main.py` 做冒烟检查，其中有向预设用户发送私信的代码；quick_publish.py 也有外部副作用。

## 验证

```bash
.venv/bin/python -m pytest web/tests -q
node --check web/static/app.js
node --test web/static/utils.test.mjs
```

测试用临时数据和替身，不调用真实抖音。浏览器演示验收覆盖跨页选择、创建不发送、预约等待、开始、暂停、刷新与恢复、大盘。变更后重跑相关测试，未真实验证的账号能力不报告为成功。

不要覆盖 `.env`、`.env.example` 或用户已有 outputs。本轮真实发送、扫码与外部资料尚未联调。既有代码的登录构造器可能读取 .env，但 Web 显式指定凭证字段，避免借用其他账号票据。

2046 二次验证入口位于 `web/login.py`、`web/accounts.py` 和 `web/static/verification*`。后台保留原 Auth/token，官方回调后合并 decision.biz_params 并重新签名重试，仍检查 confirmed 和 UID。最长等待 5 分钟，不把组件回调直接当登录成功。

官方组件在同端口 `verification.localhost` 独立窗口中运行，先加载固定版本 `uc-account-second-verification-web/1.0.16` SDK 准备 React 依赖。独立来源仅允许 `/verification-frame`、`/static/verification-frame.js` 和受活动验证 ID 约束的 `/verification-request/<id>/...`，禁止所有控制台 API；主来源不提供组件页面。窗口允许自己的 Cookie 和存储，仍保留 sandbox，消息核验来源、窗口对象和验证 ID。不能改回同源 iframe 并直接放开 allow-same-origin；跨站 iframe 还会受第三方 Cookie 限制。

已修复实际 `zijieapi.com` 域名遗漏和官方依赖缺失。真实 leo 扫码已显示短信、手机刷脸、登录密码等官方身份验证选项，随后原 iframe 触发 Cookie 沙箱异常。新窗口的 Cookie 读写、控制页隔离及回调已用独立 Chrome、真实 SDK 与受控组件验证。后续真实请求定位到组件直连 `login.douyin.com` 的 CORS 错误，已通过本机后端转发修复：`web/verification.py` 固定官方主机、精确限制接口与方法，使用触发 2046 的原扫码 Auth，不接受浏览器 Cookie 或任意主机，不跟随重定向、不回传上游 Cookie；验证结束后不能继续请求。转发路径不写访问日志，避免泄漏参数和验证 ID。真实官方请求代码加模拟 Auth 的 Chrome 回归确认旧路径 Network Error、新路径成功。2026-09-08 真实 leo 短信验证已跑通：send_code、validate_code 返回 200，原轮询 confirmed、UID 校验通过，账号 ready 且 can_send=true。本轮未验证 leo 真实发送，其他二次验证方式未真实联调。`verification.localhost` 的本机解析需浏览器支持，Chrome 已作为验证目标。诊断只保存白名单阶段，不保存票据、Cookie 或异常原文。

任务执行配置支持自动分配共享池账号或指定一个/多个账号、预约、累计/连续失败上限（默认均为 1000，现有值保留）。累计与连续失败都严格超限才暂停任务；连续次数按结果 events.id 顺序计算，成功清零，跳过不改变。快照包含 consecutive_errors 与账号状态。

任务配置允许修改 execution_mode/account_ids（兼容单个 account_id），运行中变更只影响后续领取，在途结果保留实际账号。task_accounts 保存指定集合，自动模式 account_id=NULL。recipients.sender_account_id 保存实际发送归属，历史和筛选据此统计。领取在 BEGIN IMMEDIATE 内检查模式、借调、账号就绪与等待，按 last_dispatched 轮转。

发送节奏归 accounts.send_policy：max_batch_size（每次随机发送人数上限 X，新账号默认 2）、interval_seconds/random_extra_seconds（新账号默认 200/100 秒）。每账号持锁执行一轮，逐条跨任务领取，批末等待；每次结果保留账号 next_send_at，暂停/重启不能清空。新账号错误率保护默认最近30分钟发送至少10次且错误率严格大于50%时休息30分钟；发送次数含成功/失败/在途，比例只用 failed/(sent+failed)。rest_until/error_window_start 持久化，休息结束后的新样本参与保护判断。手动暂停与鉴权状态独立。完整规则见 docs/multi-account-tasks.md。

发送状态修复：发送前 AccountIssue 诊断区分 create_conversation / identity_token，保留白名单 cause_reason 与 exception_type，不记录异常原文。网络超时/异常不撤销 can_send，仍计 failed 并按任务阈值执行；真正提交后的异常也记 failed。后续 sent 仅清除账号 ready、can_search、qr confirmed 状态下指定的旧发送检查错误，并恢复 can_send。RealAdapter.ready 的 None 表示网络未确认，账号检查保留原发送状态，扫码完成的 None 不授予发送就绪；已扫码但不可发送显示“发送待检查”。历史 account_issue 不据耗时自动重新归类。

创建会话诊断由 ConversationError 传递，覆盖 identity_lookup/request_build/request/response_decode/response_extract。记录 HTTP 状态、响应字节数/格式、外层数字状态、已知 wire 字段路径、白名单缺失字段与固定错误分类；不保存响应字符串、会话 ID、票据或消息正文。顶层字段 3 的数值独立于旧 proto 读取，字段 6/6.609 仅展开结构。创建会话成功仍返回原三元组，失败不提交消息。

发送业务结果诊断追加 business_result_state（字段缺失、空值、非法 JSON、非对象、缺业务码、业务码类型不符或 parsed）、字段长度/类型及 missing_success_fields。仅保留结构与数字，不保存原业务字符串；历史缺失诊断不能还原。详情发送日志按诊断 reason 记录，不将外层 OK 当作成功。执行配置允许运行中/暂停时换话术，recipients.send_message 保存每次领取的正文，历史接口使用该快照。

2026-09-08 发送结果统一为 sent/failed。`utils/send_diagnostics.py` 集中维护业务码的展示原因：7180 使用已有业务含义，其他失败业务码显示“平台返回错误码 XX”。超时、缺字段、解析失败同样记 failed，但保留具体原因且不重发。启动时在同一事务迁移旧 uncertain，并保持已有结果事件的 ID/时间和原 send_diagnostic，以更新曲线、连续失败、账号错误率；前端和 API 不再返回第三种发送终态或其统计项。内部 SendUncertain 仅承载缺少成功凭据的诊断，最终仍存 failed。

发送详情页面 `/send-details` 已独立接入导航；`web/send_records.py` 提供 `/api/send-records` 只读历史检索，复用逐条正文与最新脱敏诊断。支持账号、任务、结果、业务码/未记录、UID、关键词、上海日期及分页；已删除任务历史保留。旧记录没有逐条正文时明确标识旧任务正文；不推测业务码 0。用户管理“历史”跳转并带 UID。状态码数字通过明确白名单返回，不回传完整诊断或凭证。

4002、8101 成功口径（2026-09-08）：用户从抖音 App 观察到消息可见且可能收到回复，明确要求两码计成功。`BUSINESS_SUCCESS_CODES` 维护该集合，解析 reason 为 business_accepted，Web 保存 sent 并保留原码；7173、7278、7911 仍为 failed。这属于产品统计口径，不宣称官方错误码含义或保证送达。历史按最新 send_diagnostic 数字业务码一次性转为 sent，清除当前失败说明，sent_at 使用原结果事件时间（缺事件时使用原诊断时间），保留原诊断及事件 ID/时间。失败计数、连续次数、错误率及历史曲线同步变化；不重发、不自动恢复任务或清空账号休息。

共享池自动参与（2026-09-08）：取消 pool_enabled 配置和公开字段，新老账号均默认参与；领取时仍检查发送开关、鉴权、冷却、休息、在途锁及指定任务借调。历史数据库中的 pool_enabled 列只为兼容保留，值不再影响调度。PATCH 发送配置不再接受此字段。自动任务等待期间，未就绪账号允许扫码鉴权；已就绪且可能被领取的账号修改鉴权前仍须暂停该账号调度。

账号发送配置复制（2026-09-10）：Accounts.add 显式保存固定系统默认 send_policy（间隔 200 秒、随机等待上限 100 秒、每批上限 2 人、至少 10 次且错误率大于 50% 时休息 30 分钟），不读取“个人账号”，不修改已有账号。历史缺字段使用 LEGACY_SEND_POLICY 保留原行为。发送配置弹窗可选择其他账号导入 6 项参数，保存前仅影响表单，保存复用原接口。暂停开关、冷却、休息及鉴权不参与复制；发送区域原位置的各调度状态统一为彩色圆角标签，保留原卡片布局及按钮。冷却中和休息中右侧按 available_at（冷却、休息的较晚时间）显示秒级发送倒计时，归零等待调度，切换状态后移除倒计时；仅前端展示，不改变调度。

总览账号倒计时与业务码分布（2026-09-10）：`web/overview.py` 在同一只读事务补充运行账号 available_at、next_send_at，并按所选近 7／30 天的 attempted_at 聚合成功／失败发送的最新整数业务码，缺码单列，不推测码 0，不改变发送结果。前端运行账号区复用状态标签与倒计时，按服务器 now 校准；横向堆叠条形图展示各码数量、占比及实际成功／失败组成。

2026-09-10：`accepted_without_business_code` 集中判断上述缺业务码成功例外，要求诊断明确 `business_result_state=missing` 且 `missing_success_fields=[business_code]`，不补业务码 0。`response_diagnostic` 接收 HTTP 状态；Web 接受 `accepted_without_business_code`。Tasks 历史迁移标记 `business_success_missing_v1`，保留诊断和事件时间，不重发、不清除休息。发送详情及任务接收者 `result_note` 使用 `BUSINESS_CODE_NOTES` 固定说明，推测与未知明确标注，不改变其他码的结果。

2026-09-10 后续口径：用户确认 21003、31003 也计成功，`BUSINESS_SUCCESS_CODES={4002,8101,21003,31003}`。原码和原因未确认标注保留；新增历史迁移标记 `business_success_21003_31003_v1`，沿用原诊断/事件时间保留、不重发、不自动恢复任务的规则。

## 2026-09-10 双向消息历史

`dy_apis/douyin_im_history.py` 封装已验证的 cmd301 会话历史分页，入口 `DouyinAPI.get_conversation_messages`；复用单账号 Auth 和签名，只读、不创建会话、不标记已读。`web/messages.py` 提供用户消息查询及每次一页的同步，通过账号锁隔离，message_conversations 与 conversation_messages 同事务保存消息和独立 older/newer 游标。首次须提供已有会话 ID 与 short ID，核验双方 UID；平台 ID/游标公开时始终为字符串。网络或协议失败保留检查点，原始 content 不对前端公开。

发送详情分类 all/outgoing/incoming，默认 outgoing，仍包含成功与失败；用户管理历史跳转 UID + category=all。平台双向消息与任务发送记录按账号、平台消息 ID 去重，任务原结果优先。平台系统提示排除于交流列表，卡片摘要及未知非文本类型保留；回复不计入成功/失败统计。首次只回溯今天，读不到的账号按用户要求跳过。页面轮询当前仅刷新本地库，不代表已启动持续同步。设计与接口见 docs/message-history-backend.md、docs/web-api.md；真实回溯结果与重载验证见 docs/run.md。

## 用户对话弹窗与手动发送（2026-09-10）

`web/conversation.py` 接入本地游标分页与手动发送；`web/static/conversation.mjs` 管理弹窗会话生命周期。用户管理增加 replied 状态筛选、回复时间与“编辑 / 对话 / 历史”入口。每个弹窗只同步当前账号与 UID，5 秒一次；关闭和切换账号取消后续旧轮询，旧响应不写入新对话，上翻逐页加载。全账号后台同步按用户要求暂缓。

手动发送复用 adapter.send，chat_sends 在请求前记录 sending，结束保存 sent/failed；重复 request_id 不重发，重启只将遗留 sending 标失败。它独立于批量调度暂停、冷却、休息，不修改原调度时钟；同账号锁、有效鉴权仍检查。用户与账号的删除保护涵盖手动在途和历史。原任务历史保留，手动记录与平台回显按账号、消息 ID 去重。发送详情中 manual 为手动来源；用户已发送状态计入成功手动发送。批量任务的大盘与错误率保护仍按原 recipients 口径。

真实发送仍必须由用户明确点击提交，本轮只用模拟账号验证发送。重载与只读现场结果见 docs/run.md，计划见 docs/conversation-dialog-plan.md。


## 2026-09-11 文字与图片发送

执行器和聊天框共用 `web/outbound.py` 的消息校验与资格检查；`web/media.py` 保存已验证的本地图片，`web/douyin.py` 复用底层图片上传/发送能力。任务 config 增加 first_message、reply_messages，逐条快照与结果保存于 task_messages；历史查询用 TASK_SENDS_SQL 合并新记录与无逐条记录的旧 recipients，不重复计数。未成功联系只能单条，成功未回复跳过，同账号有有效回复才可发消息组；本地未同步到回复时不解锁，不增加全账号后台轮询。队列固定账号和内容，逐条遵守账号节奏，失败停止该联系人剩余消息，重启不重试在途消息。详情见 docs/text-image-execution.md。成功业务码当前为 4002、8101、21003、31003，严格缺业务字段情形也按已确认产品口径记 sent，均保留原诊断。

## 2026-09-11 账号批量配置与工作时间

`web/work_time.py` 验证每周工作日与单个当天时间段，按 Asia/Shanghai 计算下一允许时间；accounts.work_schedule 为可空 JSON，升级/新建默认 null（每天全天）。开始包含、结束不包含，结束允许 24:00，不支持跨午夜。`Accounts._configure_sending` 由单账号和 `/api/accounts/batch/sending` 复用，批量逐项覆盖且同事务提交，失败全量回滚，不改凭证、任务或已有冷却/休息。

调度 tick 跳过非工作时间账号，每次 `_send_next`（包括批内和图文组的下一条）仍在领取事务中复核工作时间；在途结果正常保存。公开 work_active，dispatch_state 增加 off_hours；available_at 把冷却/休息期限映射到下一允许窗口，指定任务还考虑预约。账号管理和总览复用“非工作时间”标签与倒计时，手动聊天保持原独立发送语义。

`web/static/account-settings.mjs` 提供工作时间和批量编辑弹窗。账号页多选在轮询期间保留，批量编辑仅提交勾选字段，另有批量暂停/恢复；单账号发送配置的“导入”仍仅复制原 6 项参数。范围及验证见 docs/account-work-time-plan.md、docs/run.md；本轮不对真实账号自动设置工作时段。


## 2026-09-11 任务回复回查与已回复用户

`web/reply_review.py` 支持任务运行中按 reply_check_interval_minutes 周期回查成功未回复的实际账号与 UID 组合，发现有效回复后排除后续周期候选。新任务默认 30 分钟，旧任务缺字段保持 0（关闭周期，可编辑开启），0 不关闭结束最终补查。暂停/关闭不继续周期，恢复或修改间隔重新计时；无额外查询频控，不套用发送冷却或休息。

Tasks._finish 在完成事务内创建最终全量成功回查，包含已回复者和图文部分成功。回查 generation 隔离在途周期查询，旧页可入库但不得覆盖新最终轮结果；task_reply_reviews/task_reply_targets 保存当前轮次、阶段和结果，旧 completed 任务不补建。复用 Messages.sync 和账号锁，首次覆盖最早成功发送时间再读 newer 至末页。只保存消息，不触发发送。详见 docs/task-reply-review-design.md。

任务对象返回 reply_review、reply_check_interval_minutes、reply_check_next_at；GET /api/tasks/<tid>/replies 只读返回本任务成功组合的全部已保存有效回复摘要及分页，旧任务无回查记录也可展示。任务详情已回复列表显示联系人/账号/最近内容/时间/条数，点击全部回复带 category=incoming、uid、account_id 跳转发送详情，不触发平台查询。周期 counts.replied 为本轮发现，列表total为所有已回复组合，不混用。

2026-09-11 任务后续发送记录：`web/send_records.py::task_followups` 提供只读 `/api/tasks/<tid>/followups`，按本任务每账号与 UID 首次成功发出的时间，关联其后本地任务/手动/已同步平台 outgoing 记录；包含部分成功，排除入站和在途，沿用安全投影与消息 ID 去重。前端保留原列表，新增独立分页面板，完成任务也只读本地刷新。原“发送详情”产品名称统一为“对话详情”，保留旧 URL。

2026-09-11 自动跟进修复：Tasks.enqueue_followups 根据本地有效回复为运行任务追加一次固定账号/内容队列；recipients.followup_enqueued 和 task_messages.followup 持久化防重复。旧首条先保存逐条快照再追加。最终回查收尾可补入跟进并把本次已完成任务转回运行，跟进结束后完成，不重复最终回查；已结束最终回查的旧任务不自动重启。跟进在账号锁内独立连续发送，不等待首发 batch/interval/rest，不修改首发等待期限；工作时间、预约、手动暂停、鉴权和失败阈值保留。失败/重启不重试，原首发消息历史保留。

2026-09-11 发送序号：`web/send_order.py` 与 send_orders 表保存每账号+UID累计 send_number。Tasks 和 Conversation 在提交事务内分配；初始历史按本地已记录时间一次性回填，旧 recipient/新 position=0 使用同一 record_key。message_records 统一输出，首次/跟进/手动均可显示第 N 次，失败占一次，回复不占；迟到旧平台历史无法确认次序时保持 null。平台同步只给未被本地消息 ID 去重且不早于已有末次编号的发出消息分配序号。GET 不写库。

2026-09-11 消息归属与图片：对话详情展示/筛选第 N 次发送与第 N 次对方回复，API message_stage 按方向+序号过滤后统计分页；message_number 统一输出，旧 send_number 保留。conversation_messages.reply_number 在同步事务分配，历史一次性回填；系统通知不计，迟到旧回复保持未知，不重编号。web/static/send-record-content.mjs 复用本地媒体 URL 校验，在列表和详情中预览已保存图片并提供原图入口。

2026-09-11 首发风控：Accounts.send_counts 仅纳入 task_messages 的 position=0 且 followup=0 及旧 recipient 首发；首发重试仍计入，不按全会话序号=1筛。recent/protection/protect 及总览 account_errors 共用该口径。手动聊天和跟进不计；全局历史累计和消息详情不变，既有休息不清除。

2026-09-12 自动跟进防重键改为 `(uid,account_id)`，跨任务复用 task_messages 的 followup/position/status/attempted_at；Tasks._followup_owner 定位已尝试或最早保留组，入队与领取事务都阻止其他任务重入。recover 清理重复 pending，保留已发送历史；不同账号独立，手动聊天不受此规则限制。task_message_pair 索引支持按组合查历史。

## 2026-09-12 账号发送概览

`web/static/account-performance.mjs` 将累计数据直接嵌入原账号首发区域，不挂独立报表。运行账号原指标和倒计时保留，每行下方补累计成功/失败/错误率和走势按钮。顶部近30分钟合计仍限运行账号，全部账号累计另标范围。其他账号默认折叠，走势在汇总/账号下方就地展开、一次一张，复用全局近7/30天；同一总览轮询的请求版本统一控制返回更新，保留展开与聚焦，原历史图表绑定范围隔离。

`web/account_performance.py` 的 `/api/overview/accounts` 在同一只读快照聚合所选时间与 lifetime（sent/failed/error_rate/undated）；lifetime 不随时间切换，支持未知账号与缺时间历史，不叠加平台回显。lifetime_detail_filters 不带日期，用于累计数字跳转；原 detail_filters 仍对应时间范围。成功沿用本地结果，首发标记由 TASK_SENDS_SQL.first_touch 提供。发送/风控均不改变；验证及正式服务重载边界见 docs/run.md。

## 2026-09-13 首次随机文案与空跟进停查

发送弹窗多选现有话术，`first_messages` 保存 1～20 条候选，首发领取事务随机选一条并固定到 task_messages。旧 first_message/message 及单图片兼容，跟进顺序不变。话术库不增加多版本结构。

`reply_messages=[]` 时 ReplyReviews 的 schedule/enqueue/pending/allowed/snapshot 全部停止回查，含最终补查和旧未完成轮次。清空使在途 generation 失效，已完成回查历史保留；重新配置跟进后可重新调度。任务 edit 在合并消息配置后更新回查安排。测试覆盖随机正文快照、编辑、空跟进禁查、在途停页、恢复与旧字段兼容。

2026-09-13 全量选人：chosen_ids 取消 10000 人上限，保留非空列表、字符串 UID 和去重。用户/搜索 selection 的 limit 仅校验正整数。filters.id_batches 将任务历史检查、用户批量操作和标签用户存在性校验分成每批 300 个 ID，沿用单事务原子性；不改变任务创建未开始及发送调度语义。

2026-09-13 任务列表性能：Tasks.list 在同一只读事务内先筛选、分页，再构建当前页任务快照。同页共享账号通过局部 account_snapshots 复用，每个账号只计算一次；请求结束即丢弃，下次读取仍实时计算，不改变详情与发送调度。回归覆盖分页前过滤、页外任务不计算、账号计算次数和跨请求状态更新。

2026-09-13 全局页面查询优化：总览复用同事务 account_snapshots，避免每任务和账号区重复统计。Accounts.send_counts 只查询账号/时间窗口内的首发状态，保留旧 recipient 与 task_messages 去重、实际发送账号及旧账号回退规则；recipient_attempt_window(status,attempted_at)、recipient_sender_state(sender_account_id,status) 支持窗口统计与在途检查。TASK_SEND_STATS_SQL 提供不含正文/诊断/事件查询的窄统计投影，供总览、用户发送状态与账号趋势共用；消息详情仍使用原完整历史。以上不引入跨请求缓存，不修改统计口径或发送节奏。

2026-09-13 直接扫码新增账号：Accounts.add 支持仅 name，建立 uid=NULL/status=unbound 的待扫码记录；qr 不再依赖 Cookie 预校验。_complete_qr 在 UID 校验后一次性保存身份和完整加密会话，UID 唯一约束阻止重复绑定；已绑定账号仍严格匹配，失败保留原凭证。扫码发送校验为 None 时保存已确认会话、can_send=false 并提示待检查。未绑定账户不能触发空 Cookie 状态检查，失败/中断可直接重扫。前端默认名称加扫码，Cookie 导入为可选；原重新扫码和官方二次验证入口沿用。

2026-09-13 账号卡片移除“更新 Cookie”按钮及弹窗处理，已有账号通过重新扫码更新会话。新增账号的可选 Cookie 导入及后台兼容接口保留；本次仅静态页面变更，不重启服务、不暂停任务。


## 2026-09-13 Firefox 请求模拟

DY_HTTP_IMPERSONATE=firefox147 可同步选择 Firefox 网络模拟、UA、浏览器/Gecko 参数并去除 sec-ch-ua 头；firefox 别名解析到本机支持版本，不支持的 Firefox 版本报错。默认仍为 Chrome。真实单条授权测试已完成会话创建与身份令牌获取，但发送返回 7911，保存 failed、未重试，原账号调度开关已恢复。正式服务未切换或重启，扫码与完整 Firefox 设备指纹尚未验证；详见 docs/run.md。

2026-09-13 夸克参数单条实测：从临时普通夸克窗口采集 QuarkPC 7.2.0.992 / Chrome 144 / macOS 参数，Python 以 chrome142 近似模拟。一次授权发送返回业务码 0 和消息 ID，随后平台历史查询确认同 ID/正文/发送账号；未证明接收端可见或稳定成功。平台与客户端提示新增 DY_FP_* 覆盖，正式服务未重载。元信息清单见 docs/send-request-metadata.md。
