"""Open the standalone ticket workbench in a browser."""

import os
from pathlib import Path
import socket
import sys
from threading import Timer
import webbrowser

from web.__main__ import main


def desktop_main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    data = (Path(os.environ.get("LOCALAPPDATA", Path.home() / "Library" / "Application Support")) /
            "Bifrost" / "Ticket")
    data.mkdir(parents=True, exist_ok=True)
    sys.argv = [sys.argv[0], "--port", str(port), "--data-dir", str(data)]
    Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    main()


if __name__ == "__main__":
    desktop_main()
