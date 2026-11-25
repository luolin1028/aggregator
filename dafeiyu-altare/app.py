"""AstrBot entrypoint for the Pterodactyl / Nodaroo "Python 3.12" egg.

The egg installs requirements.txt into ./.local and runs:
    /usr/local/bin/python /home/container/app.py
Wings exposes the server's primary allocation as SERVER_PORT.
"""

import os
import sys
import time
from pathlib import Path

if sys.version_info < (3, 12):
    raise SystemExit(
        "AstrBot 4.29 requires Python >= 3.12, current: "
        f"{sys.version.split()[0]}. Pick the Python 3.12 docker image."
    )

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# pip --prefix .local puts packages here; make sure they are importable
# even if HOME is not /home/container.
site_packages = (
    ROOT / ".local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
)
for p in (site_packages, ROOT / ".local" / "lib64" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

# The node is in Europe; keep the bot's schedule in China time.
os.environ.setdefault("TZ", "Asia/Shanghai")
if hasattr(time, "tzset"):
    time.tzset()

# AstrBot treats the current directory as its root and requires this marker.
(ROOT / ".astrbot").touch()
for rel in ("data", "data/config", "data/plugins", "data/temp"):
    (ROOT / rel).mkdir(parents=True, exist_ok=True)

# Pterodactyl gives the public port as SERVER_PORT.
port = (
    os.environ.get("SERVER_PORT")
    or os.environ.get("PORT")
    or os.environ.get("DASHBOARD_PORT")
    or "6185"
)
os.environ["DASHBOARD_PORT"] = str(port)

print(f"[dafeiyu] starting AstrBot WebUI on 0.0.0.0:{port}", flush=True)

sys.argv = ["astrbot", "run", "--port", str(port)]

from astrbot.cli.__main__ import cli  # noqa: E402

cli()
