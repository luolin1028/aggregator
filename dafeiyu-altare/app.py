# AstrBot entrypoint for the Pterodactyl / Nodaroo Python egg (v2).
# The egg's own pip runs with a tiny /tmp; this script installs AstrBot with
# TMPDIR pointed at the server volume instead.
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if sys.version_info < (3, 12):
    raise SystemExit("AstrBot 4.29 needs Python >= 3.12")

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
os.environ.setdefault("TZ", "Asia/Shanghai")
if hasattr(time, "tzset"):
    time.tzset()


def site_dir():
    return ROOT / ".local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def add_paths():
    for p in (site_dir(), ROOT / ".local"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def show_disk(path):
    total, used, free = shutil.disk_usage(path)
    print(f"[dafeiyu] {path}: total={total / 2**30:.2f}G free={free / 2**30:.2f}G", flush=True)


def ensure_astrbot():
    add_paths()
    try:
        import astrbot  # noqa: F401

        return
    except ModuleNotFoundError:
        pass
    tmp = ROOT / ".tmp"
    tmp.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp)
    env["PIP_NO_CACHE_DIR"] = "1"
    print(f"[dafeiyu] installing astrbot, TMPDIR={tmp}", flush=True)
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
        raise SystemExit("[dafeiyu] pip install failed, see the log above")
    add_paths()


for d in ("data", "data/config", "data/plugins", "data/temp"):
    (ROOT / d).mkdir(parents=True, exist_ok=True)

print("[dafeiyu] app.py v2", flush=True)
for p in ("/tmp", str(ROOT)):
    try:
        show_disk(p)
    except OSError as exc:
        print(f"[dafeiyu] {p}: {exc}", flush=True)

ensure_astrbot()

port = os.environ.get("SERVER_PORT") or os.environ.get("PORT") or "6185"
os.environ["DASHBOARD_PORT"] = str(port)
print(f"[dafeiyu] starting AstrBot WebUI on 0.0.0.0:{port}", flush=True)

sys.argv = ["astrbot", "run", "--port", str(port)]

from astrbot.cli.__main__ import cli  # noqa: E402

cli()
