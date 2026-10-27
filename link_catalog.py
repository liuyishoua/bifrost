"""Local link metadata only; no application discovery or network calls."""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

DIRECTORY_GROUPS = (
    ("operations", "运营工作区", "从运营中心开始，也可以展开直达各平台与运营工具。"),
    ("ai", "AI 与协作", "模型聊天、人与 AI 协作及任务管理。"),
    ("life", "生活服务", "共享点单、出行抢票等日常服务。"),
    ("games", "游戏娱乐", "随时开一局，挑战个人纪录，收集高光时刻。"),
    ("independent", "其他应用", "尚未归类的应用与工具。"),
)
CATEGORY_ALIASES = {"AI 工具": "AI 与协作", "应用": "", "工具": "运营工作区"}


def load_links(path):
    """Read only the catalog, without discovering or contacting applications."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("链接配置必须是 JSON 数组")
    links = []
    seen = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"第 {index} 个链接必须是对象")
        values = {}
        for key in ("id", "name", "url", "description", "category", "group"):
            value = entry.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"第 {index} 个链接的 {key} 必须是字符串")
            values[key] = value.strip()
        category_groups = {label: key for key, label, _ in DIRECTORY_GROUPS}
        category_groups["AI 工具"] = "ai"
        values["group"] = values["group"] or category_groups.get(values["category"], "independent")
        if values["group"] not in {key for key, _, _ in DIRECTORY_GROUPS}:
            raise ValueError(f"第 {index} 个链接的 group 必须是 operations、ai、life、games 或 independent")
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", values["id"]) or values["id"] in seen:
            raise ValueError(f"第 {index} 个链接需要唯一的 id（小写字母、数字、下划线、短横线）")
        seen.add(values["id"])
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"第 {index} 个链接的 enabled 必须是布尔值")
        values["enabled"] = enabled
        if not enabled and not values["url"]:
            if not values["name"]:
                raise ValueError(f"第 {index} 个项目需要名称")
            values["category"] = values["category"] or "其他"
            links.append(SimpleNamespace(**values))
            continue
        url = urlsplit(values["url"])
        if (not values["name"] or url.scheme not in ("http", "https")
                or not url.hostname or url.username is not None or url.password is not None
                or any(char.isspace() or ord(char) < 32 for char in values["url"])):
            raise ValueError(f"第 {index} 个链接需要名称和不含凭证的 HTTP(S) 地址")
        try:
            url.port
        except ValueError as exc:
            raise ValueError(f"第 {index} 个链接的端口无效") from exc
        values["category"] = values["category"] or "其他"
        links.append(SimpleNamespace(**values))
    return links
