# bifrost

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-orange)](LICENSE)

Bifrost（彩虹桥）是保留登录功能的链接门户。登录后浏览项目列表；搜索与分类切换在浏览器本地即时筛选，不请求后台；点击后直接打开目标服务。

首页按用途划分为“运营工作区 / AI 与协作 / 生活服务 / 游戏娱乐”，尚未归类的入口保留在“其他应用”。运营中心为运营工作区主入口，展开可直接打开平台与运营配套工具；Multica、模型聊天列在 AI 与协作；抢票和共享点单归入生活服务，虹域单独列在游戏娱乐。“全部平铺”按原顺序展示完整列表，两种视图共用同一份目录。顶部始终展示用途分类及数量，分组和平铺视图都能直接筛选，切换分类保留当前视图。全局搜索覆盖折叠入口，并在分组视图自动展开匹配项。分组、搜索及原分类筛选支持 URL 参数与浏览器前进后退，交互均不重新请求后台。目录的 `group` 配置见 [INTEGRATION.md](INTEGRATION.md)。

正式门户入口：<https://d.58nft.net/>。登录后以纵向应用列表展示 ticket、douyin、model-bridge、multica、xianyu 闲鱼工作台和 xhs 小红书工作台。原 `https://d.58nft.net:9443/` 入口重定向至正式域名。门户由 `bifrost.service` 开机启动；域名配置保存在 `d.58nft.net.caddy`，服务文件保存在 `deploy/`。域名下现有模型 `/v1/*`、`/inference/v1/*` API 路由继续保留，模型聊天网页使用 `https://d.58nft.net:9449/`。

门户负责项目列表及门户访问权限。正式环境的账号密码和会话由运营应用中心统一维护；彩虹桥与已接入运营中心的工作台共用登录。应用仍独立部署、独立启动、独立管理业务权限；门户不探测应用、不控制进程、不构建安装包、不生成业务代理配置。

## 正式环境统一登录

`bifrost.service` 设置 `OPS_IDENTITY_URL=http://127.0.0.1:8787`。未登录访问 `/login` 或 `/register` 时进入 `https://d.58nft.net:9461/login?return=bifrost`；登录成功只允许返回登记的门户页面或应用固定地址。两站共享同一主机上的 HttpOnly、Secure 会话 Cookie，服务器逐次向中心校验身份，中心不可用返回 503，不回退旧门户密码。

`/settings` 修改统一密码并撤销旧会话；运营中心顶部也提供修改密码入口。任一站退出都会撤销统一会话。旧 `portal_session` 不再用于正式环境认证。尚未接入运营中心的独立应用仍保留其认证方式，第三方平台扫码或短信登录也独立保留。

首次切换前暂停两个服务、备份各自 SQLite 数据库，执行 `deploy/import_ops_identity.py PORTAL_DB IDENTITY_DB`。同名账号保留中心现有 ID 和密码，门户独有账号将密码哈希导入中心；`identity_links` 将中心 ID 明确关联到原门户用户，门户管理员角色和状态保留。业务用户 ID 与小红书账号归属不变。迁移后不应回滚旧身份库，避免丢失新用户和恢复已撤销会话。

## 本机启动

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .runtime
chmod 700 .runtime
# 将非空的管理员密码写入 .runtime/admin-password，并设置权限为 600。
.venv/bin/python portal.py bootstrap-admin admin --password-file .runtime/admin-password
BIFROST_PUBLIC_ORIGIN=http://127.0.0.1:8790 .venv/bin/python portal.py
```

打开 `http://127.0.0.1:8790`。上述未设置 `OPS_IDENTITY_URL` 的本地开发模式仍使用独立账号。已有管理员时跳过创建步骤。默认数据库为 `.runtime/portal.sqlite3`；`BIFROST_DATA_DIR` 可指定运行目录。所有已启用账号默认看到全部项目，链接 `id` 保持稳定。

也可运行 `caddy run --config Caddyfile`，通过 `http://127.0.0.1:8080` 访问；此时 `BIFROST_PUBLIC_ORIGIN` 使用该地址（默认值）。Caddy 仅转发门户，不再导入 `.runtime/apps.*.caddy`。公网入口示例见 `Caddyfile.public`；门户设置实际的 `BIFROST_PUBLIC_ORIGIN`，Caddy 设置 `BIFROST_PUBLIC_IP`、`BIFROST_TLS_CERT`、`BIFROST_TLS_KEY`。

## 维护链接

编辑 [links.json](links.json)，保存后下次打开或刷新导航页即生效。也可通过 `BIFROST_LINKS_FILE` 指定其他 JSON 文件。

```json
[
  {
    "id": "ticket",
    "name": "抢票工作台",
    "description": "查询车票与管理抢票任务",
    "category": "应用",
    "url": "http://127.0.0.1:8767/"
  }
]
```

`id` 唯一且稳定；`name` 和 HTTP(S) `url` 必填，描述和分类可选。可以添加应用、文档或任意常用网站。管理员和普通已启用账号默认看到全部项目，新项目保存后自动出现在列表中。尚未部署网站的项目可设置 `"enabled": false, "url": ""`，只显示“未部署网站”，不生成无效跳转。统一模式的身份与授权后端由 ops-hub 提供，用户界面保留在彩虹桥，不跳入运营工作台。

配置中的地址应是用户浏览器可访问的服务地址。上述 `127.0.0.1` 示例仅适用于本机；仓库的 `links.json` 使用当前独立部署的 HTTPS 地址。门户的项目展示不授予目标网站权限，已接入应用需在后端执行中心授权；独立应用仍保留自己的登录，目录清楚标注。

## 从原平台迁移

先为每个目标服务配置独立的启动方式和可访问地址，再更新链接并切换门户与 Caddy。旧的应用端口代理、统一鉴权和抖音验证专用入口已从新版 Caddy 示例移除；需要这些入口的服务应在各自部署配置中维护。

Ticket 与 Douyin 已移至独立仓库 [ticket](https://github.com/liuyishoua/ticket) 和 [douyin](https://github.com/liuyishoua/douyin)，Bifrost 不再包含它们的业务源码。现有业务源码和数据在仓库外备份，门户 `.runtime/` 的账号数据库保留。原有 `app_registry.py`、`service_control.py`、`gateway_routes.py`、`app_packaging.py` 等工具暂留作迁移参考，门户不再导入或调用。`applications/weixin` 仅保留历史 Go 示例，不在默认导航中展示。历史工具及其测试需要额外安装 `requirements-legacy.txt`，门户本身只需要 `requirements.txt`。旧架构图及 `docs/superpowers/` 为历史设计记录。

## 验证

门户验证：

```sh
.venv/bin/python -m unittest discover -s tests -p test_portal.py -v
```

包含历史工具的完整回归：

```sh
.venv/bin/pip install -r requirements-legacy.txt
.venv/bin/python -m unittest discover -s tests -v
```

## 使用许可

**本项目仅供学习、研究、测试及其他非商业用途，不得用于商业目的。** 对本仓库中版权所有者拥有权利的代码，适用 [PolyForm Noncommercial License 1.0.0](LICENSE)；非商业用途下可依该许可证使用、修改和分享。商业使用须事先取得版权所有者的单独授权。

第三方代码、素材和依赖仍受其各自许可证约束，票务应用的依赖说明见 [Ticket 第三方依赖声明](https://github.com/liuyishoua/ticket/blob/master/THIRD_PARTY_NOTICES.md)。本仓库先前以 MIT 许可证发布的历史版本不受本次许可变更追溯限制。本项目属于**源码可见、限制商用**，不应标为 MIT 或 OSI 意义上的开源许可证。


## cv-cat 平台工作台

2026-10-10 新增五个独立工作台，门户只提供直接跳转：

| 项目 | 地址 | 主要用途 |
| --- | --- | --- |
| bilibili | https://d.58nft.net:9451/ | 视频、评论、UP 主与动态草稿 |
| weibo | https://d.58nft.net:9452/ | 搜索、博文、用户动态与文字发布 |
| jd | https://d.58nft.net:9453/ | 商品研究、价格快照与订单查询 |
| kuaishou | https://d.58nft.net:9454/ | 作品、评论、创作者与直播状态 |
| zhihu | https://d.58nft.net:9455/ | 文章、回答与楼中楼评论研究 |

工作台各自保留登录与平台会话，平台账号需进入后单独连接。服务、TLS、存储和接口适配分别保存在各项目；细节见各项目 `docs/workspace.md`。

Channel 消息中枢：https://d.58nft.net:9456/ ，独立管理平台消息队列、Multica 路由、任务归并和结果回传。

Headquarters 目标指挥部：<https://d.58nft.net:9457/>，独立管理跨平台业务目标、阶段、执行账号与 Agent 分工，追踪平台回执和有证据的业务结果。

## 共享点单与门户样式

`rift · 虹域：光轨围猎`：<https://d.58nft.net:9463/>。独立浏览器游戏，列在“游戏娱乐”中；支持游客试玩、统一账号战绩、每日同种子挑战与服务端回放核验排行榜。游戏进程、数据与 HTTPS 独立维护，参见 `../rift/README.md`。

2026-10-25 新增 `family-perks · 一家会员权益共享点单`：<https://d.58nft.net:9462/>，管理员入口 <https://d.58nft.net:9462/admin>。支持公共账号、瑞幸门店与优惠试算、管理员审批真实下单和取餐同步，并已接入麦当劳共享选店点餐、审批下单和管理员积分、优惠券与活动查询。会员接入按品牌分别核验，不自动支付。公共账号密码由管理员在该应用后台单独设置，管理员复用统一身份。

门户保留纵向列表与直接跳转，新增本地彩色品牌图标、日夜模式及手机布局。品牌图标复用运营中心现有资源，其他工具使用本地 SVG；映射位于 `app_visuals.py`，未知项目有默认图标。主题与运营中心共用浏览器的 `workspace-theme` 设置。搜索与分类继续只过滤已加载的列表，不发请求。

## 账号、角色与应用授权

彩虹桥 `https://d.58nft.net/` 负责目录、个人权限申请和用户管理界面。`/permissions` 是“我的权限”，`/admin` 是“用户与权限”，默认展示可搜索的用户列表，可在行内修改身份和启停状态、删除用户；点击“管理权限”在弹窗勾选应用。另有申请审批及操作记录。运营工作台 `:9461/platform-select` 只提供全部工作台、按任务开始和 AI 对话；不要把门户的权限页和用户管理页放进运营工作台。旧的 `?view=access/admin` 链接固定跳回门户对应页。

身份和授权数据仍由 ops-hub 后端统一保存，不建立第二套生产密码或权限表。门户通过固定本机身份服务和专用工作区令牌调用 `/api/auth/workspace/access/*`；普通浏览器访问令牌接口为 `/api/auth/access/*`。两条传输路径执行相同的服务端权限、来源和主体校验，门户表单额外校验绑定当前会话的 CSRF。

用户角色保存在 `users.role`，仅允许 `admin/user`，注册始终是普通用户，不能通过用户名、第一位注册者或请求字段获得管理员。旧数据库首次添加角色列时，仅迁移已有原始 leo 管理员；迁移完成后，权限取决于数据库角色，不能再写死用户 ID。管理员可管理管理员及普通用户，可升降角色、启停账号并直接勾选应用，无需等待申请。修改角色或状态撤销旧统一会话；系统至少保留一位启用的管理员。修改使用账号版本检查并串行核验最后管理员，过期表单拒绝覆盖别人更新。

新用户登录后默认开放运营工作台与游戏；受限应用由管理员直接分配或用户提交用途申请。支持审批、拒绝原因、重新申请、收回权限及审计。个人工作台继续按账号隔离。抢票、旧抖音发送、Multica、平台账号维护、经营目标和消息调度是现有共享工作空间；界面说明共享范围，获得应用权限后读取同一共享空间，不创建空白租户伪装成功。申请共享应用要求明确确认，管理员直接授权时同样显示共享提示。

用户只能查看和申请自己的权限，不能读取其他账号清单、修改自己角色或调用管理员接口。应用后端或全路径 HTTPS 代理每次执行授权校验，直达网址不能绕过；收回应用权限在后续请求生效。停用账号和角色变更递增统一撤销版本，旧访问令牌、刷新令牌和 Cookie 失效；不取消已执行的第三方业务和已有机器凭据。

点单、模型聊天与游戏共用中央账号登录。游戏默认开放，点单和模型聊天需授予应用权限。第三方平台扫码、会员凭据和支付授权属于各应用自身的业务认证。

迁移只添加 `users.role/account_revision/status` 和授权表，保留 ID、密码哈希、业务归属与认证版本。部署前备份 SQLite；回滚必须保留新的身份数据库和撤销版本。

验证：在 `ops-hub/frontend` 执行 `npm run build`；在 `ops-hub` 执行 `.venv/bin/python -m pytest tests -q`、`.venv/bin/python tests/browser/access_preview.py`。后者同时启动临时身份和门户数据库，用真实 Bifrost 表单及后端测试直接授权、管理员管理管理员、普通用户拒绝、审批与撤权，再以真实 Caddy 配置和合成后端检查共享入口。截图保存在 `ops-hub/.local/access-qa/`；不创建生产测试账号，不执行真实平台、支付或模型操作。

删除用户会撤销所有会话、清除应用授权与申请，并从用户列表移除。为防止新账号继承订单、聊天及外部身份映射，中央库保留不可登录的身份存根（状态 `deleted`、无有效密码），ID 和用户名不再分配，业务记录及审计保留。不能删除当前登录账号，也不能删除最后一位启用管理员。删除使用版本与用户名确认，并与其他账号变更串行执行。密码只要求非空，允许 1 位，注册、登录、改密与前端表单保持一致。

2026-10-27 用户体验更新：顶部账号菜单、移动端导航、应用可用状态、仅看可用应用，以及按中央用户 ID 隔离的浏览器本地收藏（只保存应用 ID，不保存凭据）。权限页加入应用图标、状态计数及空状态引导；个人设置提供账号信息和密码显示/隐藏。成功操作用可关闭、4.5 秒后消失的轻提示，展示后清理 URL，刷新不重复。错误保留原因，不自动消失。

“我的权限”首次加载当前用户的完整应用状态，之后的全部/已开通/审批中/可申请切换和搜索均在页面内完成，无新增网络请求，保留申请草稿及展开状态。筛选同步 URL 并支持前进后退、直接链接、中文输入法；关闭 JavaScript 时仍通过普通链接和 GET 搜索工作。主动“刷新”重新向中心获取最新权限并保留筛选。列表只存在当前文档，不在浏览器存储中缓存授权，提交申请及应用访问仍由后端核验。回归：`.venv/bin/python -m unittest discover -s tests -p test_permission_filters.py -v`；使用安装 Playwright 的 Python 执行 `tests/browser/permission_filters_check.py`，以临时数据库、合成会话验证零请求切换、草稿、前进后退、刷新、真实 CSRF 表单和 320–1440px 布局。

“用户与权限”的用户管理、权限申请和操作记录也在首次请求时统一渲染，之后本地切换，无新增网络请求。切换保留用户搜索、未保存的身份/状态选择和审批说明，同步 URL 并支持前进后退与直接链接；无 JavaScript 时退回普通链接。离开用户页签自动遮住已查看的密码，忽略尚未完成的密码读取结果，历史导航时关闭打开的管理弹窗。“刷新列表”重新读取中央数据并保留当前页签；审批、授权及账号操作继续走原有 CSRF 表单、权限层级和版本检查。回归：`.venv/bin/python -m unittest discover -s tests -p test_admin_tabs.py -v`；使用安装 Playwright 的 Python 执行 `tests/browser/admin_tabs_check.py`，仅使用临时数据库与合成会话。

用户列表的“编辑账号”可改其他用户的用户名、重设密码（非空，允许 1 位）；输入框支持显示/隐藏。密码校验继续使用不可逆哈希；按用户明确要求，新增独立加密副本供管理员点击查看（规则见下文）。修改后撤销旧会话，保持用户 ID 与业务数据归属；旧用户名写入 `reserved_usernames`，避免历史身份被重新注册占用。

`users.is_superadmin` 是显式指定的持久保护标记，角色对业务应用仍为 `admin`。按用户明确指定，生产 ID 1（leo）由 `deploy/promote_superadmin.py` 设为超级管理员；运行时不按用户名猜测身份。所有管理员接口拒绝修改超级管理员的身份、状态、应用权限、账号密码或删除；本人改密仍走个人设置。只有超级管理员可设置或撤销管理员身份；普通管理员只能管理普通用户，不能管理同级管理员、自己或超级管理员。管理员自动拥有全部已接入应用的权限。新用户不能通过注册或角色更新设置超级管理员标记。

用户体验验收：`HUB_ACCESS_SUPERADMIN=1 HUB_ACCESS_BROWSER_CHECK=/home/ubuntu/leo-project/bifrost/tests/browser/experience_check.cjs .venv/bin/python tests/browser/access_preview.py`（在 ops-hub 执行），只使用隔离数据库与合成账号。验收覆盖收藏隔离、1 位密码、改名改密、超级管理员保护、角色提权、刷新不重现成功提示，以及 320–1440 宽度和日夜模式。

2026-10-27 按用户明确要求，管理员可在用户列表“登录密码”列点击显示/隐藏低于自己级别账号的密码。`user_password_vault` 只保存 Fernet 密文与当前密码哈希的指纹，密钥独立保存在身份数据库同目录 `password-vault.key`（0600），不能替换已有密钥。备份和恢复需同时保留身份数据库与该密钥，密钥不得放入网页、日志或版本库。原 `password_hash` 仍是登录校验依据。注册、用户改密、管理员重设密码同步加密副本；历史账号下次正常登录后补齐，不修改其密码、用户 ID 或现有授权。未同步时显示“待登录同步”，无法从旧哈希逆推出原文。

密码不会随用户列表返回，只通过绑定会话的 POST + Origin/CSRF 校验后按需读取，响应 `private, no-store`，审计只记录查看动作。前端显示 30 秒后、切换窗口或页面离开时自动遮住，不写入浏览器存储。超级管理员密码不提供查看，普通管理员不能查看同级管理员密码。删除用户同时清除其加密副本。缺失密钥或密文校验失败时拒绝读取，已有密文存在时不会创建替代密钥，密码重设也不会部分提交。
