#!/usr/bin/env python3
"""nmbot_deploy_smoke — проверяет, что live Telegram bot запущен на prod VPS.

Зачем: локальные тесты могут быть зелёными, а Telegram может крутить старый
процесс. По умолчанию проверяем настоящий production service на VPS:
`novostroy-bot.service` должен быть active, MainPID должен запускать
`scripts/chat_tester_bot.py`, а не старый `python3 -m src.bot`.

Для отладки старого локального поведения можно задать:
  NMBOT_DEPLOY_MODE=local python3 scripts/nmbot_deploy_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
WATCHED_FILES = [
    REPO / "scripts" / "chat_tester_bot.py",
    REPO / "prompts" / "chat_v1.txt",
    REPO / "prompts" / "search_v1.txt",
]
PROC_MATCH = "scripts/chat_tester_bot.py"

VPS_HOST = os.getenv("NMBOT_VPS_HOST", "193.107.155.236")
VPS_PORT = os.getenv("NMBOT_VPS_PORT", "1905")
VPS_USER = os.getenv("NMBOT_VPS_USER", "neiro")
VPS_PATH = os.getenv("NMBOT_VPS_PATH", "/home/neiro/novostroy-bot")
VPS_SERVICE = os.getenv("NMBOT_VPS_SERVICE", "novostroy-bot.service")


@dataclass
class BotProcess:
    pid: int
    start_epoch: float
    cmdline: str


def _boot_time() -> float:
    with open("/proc/stat", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("btime "):
                return float(line.split()[1])
    raise RuntimeError("cannot read btime from /proc/stat")


def _clock_ticks() -> int:
    return os.sysconf(os.sysconf_names["SC_CLK_TCK"])


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _process_start_epoch(pid: int) -> float:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    # comm может содержать пробелы в скобках, starttime — 22-е поле после pid.
    after_comm = stat.rsplit(")", 1)[1].strip().split()
    start_ticks = int(after_comm[19])
    return _boot_time() + (start_ticks / _clock_ticks())


def find_bot_processes() -> list[BotProcess]:
    current = os.getpid()
    procs: list[BotProcess] = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        pid = int(p.name)
        if pid == current:
            continue
        cmd = _cmdline(pid)
        if PROC_MATCH not in cmd:
            continue
        try:
            procs.append(BotProcess(pid=pid, start_epoch=_process_start_epoch(pid), cmdline=cmd))
        except (FileNotFoundError, ProcessLookupError, ValueError):
            continue
    return sorted(procs, key=lambda x: x.start_epoch, reverse=True)


def newest_watched_mtime() -> tuple[Path, float]:
    existing = [(p, p.stat().st_mtime) for p in WATCHED_FILES]
    return max(existing, key=lambda item: item[1])


def _local_smoke() -> int:
    processes = find_bot_processes()
    newest_file, newest_mtime = newest_watched_mtime()

    if not processes:
        print("FAIL: nmbot live process not found locally (scripts/chat_tester_bot.py)")
        print(f"newest watched file: {newest_file} @ {time.ctime(newest_mtime)}")
        return 1

    if len(processes) > 1:
        print("FAIL: multiple local nmbot live processes found:")
        for proc in processes:
            print(f"  pid={proc.pid} started={time.ctime(proc.start_epoch)} cmd={proc.cmdline}")
        return 1

    proc = processes[0]
    if proc.start_epoch < newest_mtime:
        print("FAIL: local nmbot live process is stale; restart required")
        print(f"  pid={proc.pid} started={time.ctime(proc.start_epoch)}")
        print(f"  newest watched file={newest_file} modified={time.ctime(newest_mtime)}")
        return 1

    print("OK: local nmbot live process is fresh")
    print(f"  pid={proc.pid} started={time.ctime(proc.start_epoch)}")
    print(f"  newest watched file={newest_file} modified={time.ctime(newest_mtime)}")
    return 0


REMOTE_CHECK = r'''
import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path(os.environ["NMBOT_VPS_PATH"])
service = os.environ["NMBOT_VPS_SERVICE"]
watched = [
    root / "scripts" / "chat_tester_bot.py",
    root / "prompts" / "chat_v1.txt",
    root / "prompts" / "search_v1.txt",
]

def run(cmd):
    return subprocess.run(cmd, text=True, capture_output=True)

def fail(msg, **extra):
    print(json.dumps({"ok": False, "message": msg, **extra}, ensure_ascii=False))
    sys.exit(1)

missing = [str(p) for p in watched if not p.exists()]
if missing:
    fail("watched files missing on VPS", missing=missing)

state = ""
stderr = ""
for _ in range(20):
    active = run(["systemctl", "--user", "is-active", service])
    state = active.stdout.strip()
    stderr = active.stderr.strip()
    if state == "active":
        break
    time.sleep(1)
if state != "active":
    fail("service is not active", service=service, state=state, stderr=stderr[-500:])

pid_proc = run(["systemctl", "--user", "show", service, "-p", "MainPID", "--value"])
pid_text = pid_proc.stdout.strip()
if not pid_text.isdigit() or int(pid_text) <= 0:
    fail("service has no MainPID", service=service, main_pid=pid_text, stderr=pid_proc.stderr.strip()[-500:])
pid = int(pid_text)

cmd_path = Path(f"/proc/{pid}/cmdline")
if not cmd_path.exists():
    fail("MainPID process is not present", pid=pid)
cmdline = cmd_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
if "scripts/chat_tester_bot.py" not in cmdline:
    fail("MainPID is not chat_tester_bot.py", pid=pid, cmdline=cmdline)
if "python3 -m src.bot" in cmdline or "src.bot" in cmdline:
    fail("old src.bot process is still used", pid=pid, cmdline=cmdline)

pgrep = run(["pgrep", "-af", "chat_tester_bot.py|python3 -m src.bot"])
lines = [line for line in pgrep.stdout.splitlines() if line.strip()]
bad_src = [line for line in lines if "python3 -m src.bot" in line or "src.bot" in line]
if bad_src:
    fail("old src.bot process is running", pid=pid, bad_processes=bad_src)

stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
after_comm = stat.rsplit(")", 1)[1].strip().split()
start_ticks = int(after_comm[19])
clk = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
btime = 0.0
for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
    if line.startswith("btime "):
        btime = float(line.split()[1])
        break
if not btime:
    fail("cannot read VPS boot time", pid=pid)
start_epoch = btime + (start_ticks / clk)
newest = max(((p, p.stat().st_mtime) for p in watched), key=lambda x: x[1])
if start_epoch < newest[1]:
    fail(
        "service process is older than watched bot code/prompts; restart required",
        pid=pid,
        started=time.ctime(start_epoch),
        newest_file=str(newest[0]),
        newest_mtime=time.ctime(newest[1]),
        cmdline=cmdline,
    )

print(json.dumps({
    "ok": True,
    "service": service,
    "pid": pid,
    "cmdline": cmdline,
    "started": time.ctime(start_epoch),
    "newest_file": str(newest[0]),
    "newest_mtime": time.ctime(newest[1]),
    "matching_processes": lines,
}, ensure_ascii=False))
'''


def _vps_smoke() -> int:
    ssh_target = f"{VPS_USER}@{VPS_HOST}"
    remote_env = f"NMBOT_VPS_PATH={VPS_PATH!r} NMBOT_VPS_SERVICE={VPS_SERVICE!r} python3 -"
    cmd = [
        "ssh",
        "-p",
        VPS_PORT,
        "-o",
        "BatchMode=yes",
        ssh_target,
        remote_env,
    ]
    proc = subprocess.run(
        cmd,
        input=textwrap.dedent(REMOTE_CHECK),
        text=True,
        capture_output=True,
        timeout=int(os.getenv("NMBOT_DEPLOY_SSH_TIMEOUT", "30")),
    )
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        print("FAIL: nmbot VPS deploy smoke failed")
        print(output[-2000:])
        return proc.returncode or 1

    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        print("FAIL: cannot parse VPS smoke output")
        print(output[-2000:])
        print(f"parse_error={exc}")
        return 1

    if not data.get("ok"):
        print("FAIL: nmbot VPS deploy smoke failed")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 1

    print("OK: nmbot VPS service is active and fresh")
    print(f"  service={data['service']}")
    print(f"  pid={data['pid']} started={data['started']}")
    print(f"  cmd={data['cmdline']}")
    print(f"  newest watched file={data['newest_file']} modified={data['newest_mtime']}")
    return 0


def main() -> int:
    mode = os.getenv("NMBOT_DEPLOY_MODE", "vps").strip().lower()
    if mode == "local":
        return _local_smoke()
    if mode != "vps":
        print(f"FAIL: unknown NMBOT_DEPLOY_MODE={mode!r}; expected 'vps' or 'local'")
        return 1
    return _vps_smoke()


if __name__ == "__main__":
    sys.exit(main())
