#!/usr/bin/env python3
"""Switch an already-installed immutable API release on the fixed TEST host."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from typing import Any, Protocol

HOST = "neiro@193.107.155.236"
PORT = "1905"
ROOT = "/home/neiro/novostroy-bot"
SERVICE = "novostroy-bot-api.service"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class Runner(Protocol):
    def run(self, command: str) -> subprocess.CompletedProcess[str]: ...


class SSHRunner:
    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", PORT, HOST, command], text=True, capture_output=True, check=False)


def validate_release_id(value: str) -> str:
    value = str(value or "").strip()
    if not SAFE_ID.fullmatch(value) or value in {".", ".."} or value.startswith("-"):
        raise ValueError("release id must be a safe basename")
    return value


REMOTE = r'''
import json,os,pathlib,re,shutil,subprocess,sys,time,urllib.request,uuid
c=json.loads(sys.argv[1]); root=pathlib.Path(c["root"]); releases=root/"releases"; cur=root/"current"; prior=root/"previous"; ext=root/"data/nmbot_release_identity.json"; lock=root/".release_switch_lock"; safe=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"); hex64=re.compile(r"^[0-9a-f]{64}$")
def out(**x): print(json.dumps(x,sort_keys=True)); return x
def die(msg,**x): out(status="error",error=msg,**x); raise RuntimeError(msg)
def manifest(path,rid):
 if not path.is_file() or path.is_symlink(): die("identity manifest missing")
 try: raw=path.read_bytes(); d=json.loads(raw.decode("utf-8"))
 except Exception: die("identity manifest invalid")
 if not isinstance(d,dict) or d.get("schema")!="nmbot.release_identity.v1" or d.get("release_id")!=rid: die("identity manifest mismatch")
 rows=d.get("tracked_files")
 if not isinstance(rows,list) or not rows or any(not isinstance(x,dict) or not isinstance(x.get("path"),str) or not hex64.fullmatch(str(x.get("sha256",""))) for x in rows): die("identity manifest tracked file shape invalid")
 return raw
def release(rid):
 if not safe.fullmatch(rid): die("unsafe release id")
 p=releases/rid
 if not p.is_dir() or p.is_symlink() or p.resolve()!=p: die("release directory missing or symlink")
 manifest(p/"release_identity/nmbot_release_identity.json",rid); return p
def previous():
 if not prior.is_symlink(): die("no unambiguous previous release; use --switch-to")
 link=os.readlink(prior)
 if not safe.fullmatch(pathlib.PurePosixPath(link).name) or link!="releases/"+pathlib.PurePosixPath(link).name: die("previous marker is not an exact release link")
 rid=pathlib.PurePosixPath(link).name; p=release(rid)
 if prior.resolve()!=p: die("previous marker target mismatch")
 return rid,p
def current():
 if not cur.is_symlink(): die("current is not a symlink")
 p=cur.resolve()
 if p.parent!=releases.resolve() or p.name not in {x.name for x in releases.iterdir() if x.is_dir() and not x.is_symlink()}: die("current target is not an exact release")
 local=manifest(p/"release_identity/nmbot_release_identity.json",p.name); external=manifest(ext,p.name)
 if external!=local: die("external identity bytes do not match current release")
 return p.name,p
def health(rid):
 deadline=time.time()+15; last=""
 while time.time()<deadline:
  try:
   d=json.loads(urllib.request.urlopen("http://127.0.0.1:8088/health",timeout=2).read().decode()); state=subprocess.run(["systemctl","--user","is-active",c["service"]],text=True,capture_output=True).stdout.strip()
    if d.get("ok") is True and state=="active":
     active,_=current()
     if active==rid: return {"ok":True,"service":state}
     last="release identity"
   last="health/service"
  except Exception as e: last=type(e).__name__
  time.sleep(1)
 return {"ok":False,"error":last}
def atomic_link(rid):
 tmp=root/".current.switch."+uuid.uuid4().hex+".tmp"
 try: os.symlink("releases/"+rid,tmp); os.replace(tmp,cur)
 finally: tmp.unlink(missing_ok=True)
def atomic_previous(rid):
 tmp=root/(".previous.switch."+uuid.uuid4().hex+".tmp")
 try: os.symlink("releases/"+rid,tmp); os.replace(tmp,prior)
 finally: tmp.unlink(missing_ok=True)
def atomic_identity(src):
 tmp=ext.with_name(".nmbot_release_identity.switch."+uuid.uuid4().hex+".tmp")
 try: shutil.copyfile(src,tmp); os.chmod(tmp,0o600); os.replace(tmp,ext)
 finally: tmp.unlink(missing_ok=True)
def atomic_identity_bytes(raw):
 tmp=ext.with_name(".nmbot_release_identity.switch."+uuid.uuid4().hex+".tmp")
 try: tmp.write_bytes(raw); os.chmod(tmp,0o600); os.replace(tmp,ext)
 finally: tmp.unlink(missing_ok=True)
op=c["op"]; target=c.get("target")
try:
 prev,_=current()
 if op=="status":
  release(target)
  if target!=prev: die("requested release is not current",current=prev,target=target)
  out(status="ok",current=prev,target=target,previous=None,health=health(prev)); raise SystemExit(0)
 if op=="rollback":
   target,_=previous()
 target=release(target).name
 if target==prev: die("target is already current",current=prev,target=target)
 if not c["confirm"]: out(status="dry_run",previous=prev,target=target,current=prev,previous_marker=None); raise SystemExit(0)
 owner=uuid.uuid4().hex
 try: lock.mkdir(); (lock/"owner").write_text(owner,encoding="ascii")
 except FileExistsError: die("release switch lock already exists",previous=prev,target=target)
 try:
  prev,_=current(); target_dir=release(target)
  if target==prev: die("target is already current",current=prev,target=target)
   backup=ext.read_bytes()
  subprocess.run(["systemctl","--user","stop",c["service"]],check=True)
  state=subprocess.run(["systemctl","--user","is-active",c["service"]],text=True,capture_output=True).stdout.strip()
  if state not in ("inactive","failed"): die("api did not stop")
  atomic_link(target); atomic_identity(target_dir/"release_identity/nmbot_release_identity.json"); subprocess.run(["systemctl","--user","start",c["service"]],check=True)
   result=health(target)
   if not result["ok"]: die("target health failed")
   atomic_previous(prev)
   out(status="ok",previous=prev,target=target,current=target,previous_marker="releases/"+prev,rollback={"attempted":False},health=result); raise SystemExit(0)
 except BaseException as exc:
  rollback={"attempted":True,"ok":False}
  try:
    subprocess.run(["systemctl","--user","stop",c["service"]],check=False); atomic_link(prev); atomic_identity_bytes(backup); subprocess.run(["systemctl","--user","start",c["service"]],check=False); rollback["health"]=health(prev); rollback["ok"]=bool(rollback["health"].get("ok"))
   except Exception: pass
   out(status="error",error=str(exc),previous=prev,target=target,current=prev if rollback["ok"] else None,previous_marker=None,rollback=rollback); raise SystemExit(2)
 finally:
  try:
   if (lock/"owner").read_text(encoding="ascii")==owner: (lock/"owner").unlink(); lock.rmdir()
  except OSError: pass
except SystemExit: raise
except Exception as exc: out(status="error",error=str(exc),current=None); raise SystemExit(2)
'''


def remote_command(op: str, target: str | None, confirm: bool) -> str:
    payload = json.dumps({"root": ROOT, "service": SERVICE, "op": op, "target": target, "confirm": confirm}, sort_keys=True)
    return "python3 -c " + shlex.quote(REMOTE) + " " + shlex.quote(payload)


def execute(*, op: str, target: str | None = None, confirm: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    if target is not None:
        target = validate_release_id(target)
    proc = (runner or SSHRunner()).run(remote_command(op, target, confirm))
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        result = {"status": "error", "error": "remote returned no structured result"}
    if proc.returncode and result.get("status") != "error": result = {"status": "error", "error": "remote command failed"}
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Switch fixed TEST API immutable releases.")
    g = p.add_mutually_exclusive_group(required=True); g.add_argument("--status", action="store_true"); g.add_argument("--switch-to"); g.add_argument("--rollback", action="store_true")
    p.add_argument("--release-id", help="Required with --status")
    p.add_argument("--confirm", action="store_true")
    a = p.parse_args(argv)
    if a.status and not a.release_id: p.error("--status requires --release-id ID")
    if a.release_id and not a.status: p.error("--release-id is only for --status")
    try: result = execute(op="status" if a.status else ("rollback" if a.rollback else "switch"), target=a.release_id if a.status else a.switch_to, confirm=a.confirm)
    except ValueError as exc: result = {"status":"error","error":str(exc)}
    print(json.dumps(result, sort_keys=True)); return 0 if result.get("status") in {"ok","dry_run"} else 2


if __name__ == "__main__": raise SystemExit(main())
