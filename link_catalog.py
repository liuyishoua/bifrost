"""Local link metadata only; no application discovery or network calls."""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


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
        for key in ("id", "name", "url", "description", "category"):
            value = entry.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"第 {index} 个链接的 {key} 必须是字符串")
            values[key] = value.strip()
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", values["id"]) or values["id"] in seen:
            raise ValueError(f"第 {index} 个链接需要唯一的 id（小写字母、数字、下划线、短横线）")
        seen.add(values["id"])
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
