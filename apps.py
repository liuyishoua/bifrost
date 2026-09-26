"""Server-owned application registry. Routes must also be added to Caddyfile."""
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOUYIN = ROOT / "applications" / "douyin"
TICKET = ROOT / "applications" / "ticket"
TICKET_PYTHON = ROOT / ".venv-ticket" / "bin" / "python"
DOUYIN_PYTHON = ROOT / ".venv-douyin" / "bin" / "python"


@dataclass(frozen=True)
class App:
    id: str
    name: str
    description: str
    path: str
    port: int
    repository: Path
    data_dir: Path
    ready_path: str
    command: tuple[str, ...]


APPS = {
    "ticket": App("ticket", "抢票工作台", "查询车票与管理抢票任务", "/ticket/", 8767,
                  TICKET, TICKET / ".runtime_web",
                  "/api/state", (str(TICKET_PYTHON), "-m", "web", "--port", "8767", "--data-dir",
                                 str(TICKET / ".runtime_web"))),
    "douyin": App("douyin", "抖音流量矩阵", "账号、搜索与消息工作台", "/douyin/", 8766,
                  DOUYIN, DOUYIN / "datas" / "web", "/api/meta",
                  (str(DOUYIN_PYTHON), "-m", "web", "--port", "8766",
                   "--data-dir", str(DOUYIN / "datas" / "web"))),
}
