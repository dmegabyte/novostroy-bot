#!/usr/bin/env python3
"""nmbot_deploy_smoke — проверяет, что live Telegram bot запущен на свежем коде.

Зачем: тесты на диске могут быть зелёными, а Telegram всё ещё крутит старый
процесс. Скрипт падает, если process start time старше кода/промптов.
"""
from __future__ import annotations

import os
import sys
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


def main() -> int:
    processes = find_bot_processes()
    newest_file, newest_mtime = newest_watched_mtime()

    if not processes:
        print("FAIL: nmbot live process not found (scripts/chat_tester_bot.py)")
        print(f"newest watched file: {newest_file} @ {time.ctime(newest_mtime)}")
        return 1

    if len(processes) > 1:
        print("FAIL: multiple nmbot live processes found:")
        for proc in processes:
            print(f"  pid={proc.pid} started={time.ctime(proc.start_epoch)} cmd={proc.cmdline}")
        return 1

    proc = processes[0]
    if proc.start_epoch < newest_mtime:
        print("FAIL: nmbot live process is stale; restart required")
        print(f"  pid={proc.pid} started={time.ctime(proc.start_epoch)}")
        print(f"  newest watched file={newest_file} modified={time.ctime(newest_mtime)}")
        return 1

    print("OK: nmbot live process is fresh")
    print(f"  pid={proc.pid} started={time.ctime(proc.start_epoch)}")
    print(f"  newest watched file={newest_file} modified={time.ctime(newest_mtime)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
