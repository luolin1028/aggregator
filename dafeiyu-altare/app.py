"""AstrBot entrypoint for the Pterodactyl / Nodaroo "Python" egg.

The egg's own startup always runs `pip install` with the container's default
TMPDIR. On this host that temp space is tiny, so pip dies with ENOSPC even
though /home/container has plenty of room. This script skips that by
installing AstrBot itself with TMPDIR pointed at the server volume.

Startup settings:
    App py file        = app.py
    Requirements file  = none.txt   (skip the egg's own pip, optional)
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if sys.version_info < (3, 12):
    raise SystemExit(
        f"AstrBot 4.29 needs Python >= 3.12, current {sys.version.split()[0]}."
        " Pick a Python 3.12/3.13 docker image."
    )

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# The node is in Europe; keep the bot's schedule in China time.
os.environ.setdefault("TZ", "Asia/Shanghai")
if hasattr(time, "tzset"):
    time.tzset()


def local_site_packages() -> Path:
    return (
        ROOT
        / ".local"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def add_local_site_packages() -> None:
    for p in (
        local_site_packages(),
        ROOT / ".local" / "lib64" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
        ROOT / ".local",
    ):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def show_disk() -> None:
    g = 1024**3
    for p in ("/tmp", str(ROOT)):
        try:
            total, used, free = shutil.disk_usage(p)
            print(
                f"[dafeiyu] {p}: total={total / g:.2f}G used={used / g:.2f}G free={free / g:.2f}G",
                flush=True,
            )
        except OSError as exc:
            print(f"[dafeiyu] {p}: {exc}", flush=True)


def ensure_astrbot() -> None:
    add_local_site_packages()
    try:
        import astrbot  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    tmp = ROOT / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp)
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    print(f"[dafeiyu] astrbot missing, installing with TMPDIR={tmp}", flush=True)
    rc = subprocess.call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-U",
            "--no-cache-dir",
            "--prefix",
            str(ROOT / ".local"),
            "-r",
            str(ROOT / "requirements.txt"),
        ],
        env=env,
    )
    shutil.rmtree(tmp, ignore_errors=True)

    if rc != 0:
        raise SystemExit(
            "[dafeiyu] pip install failed. If the log still says "
            "'No space left on device', the server volume itself is too small."
        )

    add_local_site_packages()


ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / ".astrbot").touch()
for rel in ("data", "data/config", "data/plugins", "data/temp"):
    (ROOT / rel).mkdir(parents=True, exist_ok=True)

show_disk()
ensure_astrbot()

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
