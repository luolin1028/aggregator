"""Altare "Python" app entrypoint: start AstrBot with its WebUI.

Altare's Python runtime runs `python main.py` after installing
requirements.txt. AstrBot's pip package is a CLI, so this shim prepares the
working directory and forwards to `astrbot run`.
"""

import os
import sys
import time
from pathlib import Path

if sys.version_info < (3, 12):
    raise SystemExit(
        "AstrBot 4.29 requires Python >= 3.12. "
        f"Current interpreter: {sys.version.split()[0]}. "
        "Please choose a Python 3.12+ runtime on Altare."
    )

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# The server is in Stockholm; keep the bot's schedule in China time.
os.environ.setdefault("TZ", "Asia/Shanghai")
if hasattr(time, "tzset"):
    time.tzset()

# AstrBot treats the current directory as its root and requires this marker.
(ROOT / ".astrbot").touch()
for rel in ("data", "data/config", "data/plugins", "data/temp"):
    (ROOT / rel).mkdir(parents=True, exist_ok=True)

# Expose the dashboard on the port Altare injects, falling back to 6185.
port = os.environ.get("PORT") or os.environ.get("DASHBOARD_PORT") or "6185"
os.environ["DASHBOARD_PORT"] = str(port)

sys.argv = ["astrbot", "run", "--port", str(port)]

from astrbot.cli.__main__ import cli  # noqa: E402

cli()
