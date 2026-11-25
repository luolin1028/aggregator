"""One-shot disk diagnostic for the Pterodactyl Python egg.

Set Startup -> App py file = check.py (and Requirements file = none.txt to skip pip),
Start once, then read the console. Switch both values back afterwards.
"""

import os
import shutil
import subprocess

ROOT = "/home/container"
G = 1024**3

total, used, free = shutil.disk_usage(ROOT)
print(
    f"[check] disk total={total / G:.2f}GiB used={used / G:.2f}GiB free={free / G:.2f}GiB",
    flush=True,
)
print(
    f"[check] HOME={os.environ.get('HOME')} SERVER_PORT={os.environ.get('SERVER_PORT')}",
    flush=True,
)
print(f"[check] top-level={sorted(os.listdir(ROOT))[:60]}", flush=True)

for name in (".local", ".cache", "data", "wheels", "astrbot"):
    path = os.path.join(ROOT, name)
    if os.path.exists(path):
        r = subprocess.run(["du", "-sh", path], capture_output=True, text=True)
        print("[check]", (r.stdout or r.stderr).strip(), flush=True)

print("[check] done, set App py file back to app.py", flush=True)
