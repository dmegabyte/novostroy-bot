"""Safe local staging and no-clobber publication primitives for releases.

This module deliberately has no CLI, artifact, SSH, or service-management
dependencies.  The atomic release helper re-exports these functions while its
existing callers continue to own the release workflow.
"""
from __future__ import annotations

import ctypes
import errno
import os
import shutil
import tempfile
from pathlib import Path


class ReleaseError(RuntimeError):
    """A release contract could not be satisfied safely."""


def allowed_bootstrap_out_dir(out_dir: Path, *, project_root: Path) -> Path:
    original = out_dir.expanduser()
    if ".." in original.parts:
        raise ReleaseError("bootstrap output directory must not contain parent traversal")
    allowed_roots = [Path("/tmp/opencode").resolve(strict=False), (project_root / "release_bundles" / "bootstrap").resolve(strict=False)]
    if os.path.lexists(original) and original.is_symlink():
        raise ReleaseError("bootstrap output directory must not be a symlink")
    cwd = Path.cwd().resolve(strict=False)
    abs_original = original if original.is_absolute() else (cwd / original)
    if ".." in abs_original.parts:
        raise ReleaseError("bootstrap output directory must not contain parent traversal")
    allowed_root: Path | None = None
    for root in allowed_roots:
        try:
            abs_original.relative_to(root)
            allowed_root = root
            break
        except ValueError:
            continue
    if allowed_root is None:
        raise ReleaseError("bootstrap output directory must be under /tmp/opencode or project release_bundles/bootstrap")
    probe = Path(abs_original.anchor)
    for part in abs_original.parts[1:]:
        probe = probe / part
        if os.path.lexists(probe) and probe.is_symlink():
            raise ReleaseError("bootstrap output path contains a symlink component")
    return abs_original.resolve(strict=False)


def write_new_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ReleaseError(f"refusing to overwrite bootstrap output: {path}")
    path.write_text(content, encoding="utf-8")


def renameat2_syscall_number() -> int:
    machine = os.uname().machine
    if machine in {"x86_64", "amd64"}:
        return 316
    if machine in {"aarch64", "arm64"}:
        return 276
    raise ReleaseError(f"renameat2 RENAME_NOREPLACE is not supported by this platform: {machine}")


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_noreplace(src: Path, dst: Path) -> None:
    """Publish ``src`` at ``dst`` only if no lexical destination exists."""
    if not src.is_dir() or src.is_symlink():
        raise ReleaseError(f"rename_noreplace source must be a private real directory: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    syscall_no = renameat2_syscall_number()
    libc = ctypes.CDLL(None, use_errno=True)
    rename_noreplace_flag = 1
    at_fdcwd = -100
    result = libc.syscall(
        ctypes.c_long(syscall_no),
        ctypes.c_int(at_fdcwd),
        ctypes.c_char_p(os.fsencode(src)),
        ctypes.c_int(at_fdcwd),
        ctypes.c_char_p(os.fsencode(dst)),
        ctypes.c_uint(rename_noreplace_flag),
    )
    if result != 0:
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise ReleaseError(f"refusing to overwrite existing immutable release directory: {dst}")
        if err in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}:
            raise ReleaseError(f"renameat2 RENAME_NOREPLACE is not supported here; refusing unsafe publish: {dst}")
        if err == errno.EXDEV:
            raise ReleaseError(f"release publication must stay on one filesystem: {src} -> {dst}")
        raise OSError(err, os.strerror(err), str(dst))
    fsync_dir(dst.parent)


def make_private_staging_dir(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".nmbot-capture-staging-", dir=out))


def cleanup_private_staging(staging: Path, out: Path) -> None:
    try:
        staging.relative_to(out)
    except ValueError as exc:
        raise ReleaseError(f"refusing to cleanup staging outside output parent: {staging}") from exc
    if not staging.name.startswith(".nmbot-capture-staging-"):
        raise ReleaseError(f"refusing to cleanup unexpected staging path: {staging}")
    if os.path.lexists(staging) and staging.is_symlink():
        raise ReleaseError(f"refusing to cleanup symlink staging path: {staging}")
    if staging.exists():
        shutil.rmtree(staging)
