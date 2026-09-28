"""Common description of independently started applications."""
from dataclasses import dataclass
from pathlib import Path


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
    kind: str = "manifest"
    build: tuple[str, ...] = ()
    external_port: int = 0
    origin: str = ""
    activity_path: str = ""
