# bifrost

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-orange)](LICENSE)

Bifrost（彩虹桥）是保留登录功能的链接门户。登录后浏览项目列表；搜索与分类切换在浏览器本地即时筛选，不请求后台；点击后直接打开目标服务。

正式门户入口：<https://d.58nft.net/>。登录后以纵向应用列表展示 ticket、douyin、model-bridge、multica、xianyu 闲鱼工作台和 xhs 小红书工作台。原 `https://d.58nft.net:9443/` 入口重定向至正式域名。门户由 `bifrost.service` 开机启动；域名配置保存在 `d.58nft.net.caddy`，服务文件保存在 `deploy/`。域名下现有模型 `/v1/*`、`/inference/v1/*` API 路由继续保留，模型聊天网页使用 `https://d.58nft.net:9449/`。

门户负责账号、会话和默认项目列表。应用独立部署、独立启动、独立管理访问权限；门户不探测应用、不控制进程、不构建安装包、不生成业务代理配置，也不提供应用鉴权接口。

## 本机启动

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .runtime
chmod 700 .runtime
# 将至少十位的管理员密码写入 .runtime/admin-password，并设置权限为 600。
.venv/bin/python portal.py bootstrap-admin admin --password-file .runtime/admin-password
BIFROST_PUBLIC_ORIGIN=http://127.0.0.1:8790 .venv/bin/python portal.py
```

打开 `http://127.0.0.1:8790`。已有管理员时跳过创建步骤，继续使用原账号。默认数据库仍是 `.runtime/portal.sqlite3`；`BIFROST_DATA_DIR` 可指定原来的运行目录。旧账号、会话、密码和账号无需迁移；所有已启用账号默认看到全部项目，链接 `id` 仍保持稳定。

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

`id` 唯一且稳定；`name` 和 HTTP(S) `url` 必填，描述和分类可选。可以添加应用、文档或任意常用网站。管理员和普通已启用账号默认看到全部项目，新项目保存后自动出现在列表中。尚未部署网站的项目可设置 `"enabled": false, "url": ""`，只显示“未部署网站”，不生成无效跳转。注册审批、禁用账号、重置密码、首次改密、退出和操作记录继续保留。

配置中的地址应是用户浏览器可访问的服务地址。上述 `127.0.0.1` 示例仅适用于本机；仓库的 `links.json` 使用当前独立部署的 HTTPS 地址。门户的项目展示不授予目标网站权限，目标服务需要自行管理认证和访问权限。

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
