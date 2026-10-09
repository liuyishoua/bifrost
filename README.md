# Bifrost

Bifrost（彩虹桥）是保留登录功能的链接门户。登录后浏览、搜索和按分类筛选常用链接；点击后直接打开目标服务。

门户只负责账号、会话和链接可见性。应用独立部署、独立启动、独立管理访问权限；门户不探测应用、不控制进程、不构建安装包、不生成业务代理配置，也不提供应用鉴权接口。

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

打开 `http://127.0.0.1:8790`。已有管理员时跳过创建步骤，继续使用原账号。默认数据库仍是 `.runtime/portal.sqlite3`；`BIFROST_DATA_DIR` 可指定原来的运行目录。旧账号、会话、密码和链接授权无需迁移，链接 `id` 保持原来的应用 ID 即可沿用授权。

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

`id` 唯一且稳定，用于设置链接可见性；`name` 和 HTTP(S) `url` 必填，描述和分类可选。可以添加应用、文档或任意常用网站。管理员默认看到全部链接，普通用户看到管理员勾选的链接。注册审批、禁用账号、重置密码、首次改密、退出和操作记录继续保留。

配置中的地址应是用户浏览器可访问的服务地址。上述 `127.0.0.1` 示例仅适用于本机；仓库的 `links.json` 使用当前独立部署的 HTTPS 地址。链接可见性仅控制门户展示，目标服务需要自行管理认证和访问权限。

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
