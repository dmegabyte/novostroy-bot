from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("switch", ROOT / "scripts/nmbot_release_switch.py")
switch = importlib.util.module_from_spec(SPEC); assert SPEC and SPEC.loader; SPEC.loader.exec_module(switch)

class Fake:
    def __init__(self, code=0, body=None): self.code, self.body, self.commands = code, body or {"status":"ok","current":"old"}, []
    def run(self, command): self.commands.append(command); return subprocess.CompletedProcess([],self.code,json.dumps(self.body)+"\n","")

def test_release_id_validation():
    assert switch.validate_release_id("v1.2-okay") == "v1.2-okay"
    for bad in ("", "../x", "x/y", "-x", "x;rm", "x" * 81):
        try: switch.validate_release_id(bad)
        except ValueError: pass
        else: raise AssertionError(bad)

def test_fixed_batchmode_target_and_no_secrets():
    f=Fake(); switch.execute(op="status",target="old",runner=f)
    assert switch.HOST in switch.SSHRunner.__dict__["run"].__code__.co_consts or switch.HOST == "neiro@193.107.155.236"
    assert "BatchMode=yes" in str(switch.SSHRunner.run.__code__.co_consts)
    assert ".env" not in f.commands[0] and "bridge" not in f.commands[0]

def test_dry_run_and_switch_transaction_contract():
    f=Fake(body={"status":"dry_run","previous":"old","target":"new","current":"old","previous_marker":None})
    assert switch.execute(op="switch",target="new",runner=f)["status"] == "dry_run"
    command=f.commands[0]
    dry_run = command.index('if not c["confirm"]')
    assert command.index("lock.mkdir") > dry_run
    assert "os.replace(tmp,cur)" in command and "backup=ext.read_bytes()" in command
    assert "novostroy-bot-n8n-bridge" not in command

def test_remote_failures_are_structured_and_lock_cleanup_present():
    f=Fake(2,{"status":"error","error":"target health failed","rollback":{"attempted":True,"ok":True}})
    r=switch.execute(op="switch",target="new",confirm=True,runner=f)
    assert r["rollback"]["attempted"] and r["status"] == "error"
    assert "lock.rmdir" in f.commands[0] and "FileExistsError" in f.commands[0] and 'lock/"owner"' in f.commands[0]

def test_target_current_identity_and_symlink_guards_present():
    command=switch.remote_command("switch","new",True)
    for text in ("p.is_symlink()", "path.is_symlink()", "external!=local", "active==rid", "identity manifest mismatch", "target is already current", "tracked file shape", "current is not a symlink"):
        assert text in command

def test_previous_marker_is_exact_validated_release_and_written_after_health():
    command=switch.remote_command("rollback",None,True)
    assert 'link!="releases/"+pathlib.PurePosixPath(link).name' in command
    assert "p=release(rid)" in command and "prior.resolve()!=p" in command
    assert command.index('if not result["ok"]') < command.index("atomic_previous(prev)")
    assert 'previous_marker="releases/"+prev' in command

def test_failure_restores_identity_backup_without_rewriting_previous_marker():
    command=switch.remote_command("switch","new",True)
    failure = command.index("except BaseException as exc:")
    failure_body = command[failure:command.index("finally:", failure)]
    assert "atomic_identity_bytes(backup)" in failure_body
    assert "switch.backup" not in command
    assert "atomic_previous" not in failure_body
    assert 'current=prev if rollback["ok"] else None' in failure_body

def test_atomic_temp_cleanup_and_marker_toggle_contract():
    command=switch.remote_command("rollback",None,True)
    assert command.count("finally: tmp.unlink(missing_ok=True)") == 4
    assert 'target,_=previous()' in command
    assert "atomic_previous(prev)" in command

def test_no_unstructured_remote_output_or_secret_leak():
    f=Fake(1,{"status":"error","error":"release switch lock already exists"})
    assert switch.execute(op="switch",target="new",confirm=True,runner=f)["status"] == "error"
    assert "stderr" not in switch.execute.__code__.co_names
