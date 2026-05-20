"""Operation-time cwd-bound file helpers for read/write tools."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from policy.path_policy import resolve_cwd, resolve_cwd_path


def logical_mutation_key(cwd: str | Path, logical_path: str) -> Path:
    root = resolve_cwd(cwd)
    raw = Path(logical_path).expanduser()
    target = raw if raw.is_absolute() else root / raw
    return Path(os.path.normpath(str(target.absolute())))


def safe_read_bytes(cwd: str | Path, logical_path: str) -> tuple[Path, bytes]:
    root = resolve_cwd(cwd)
    raw_target = _logical_target(root, logical_path)
    resolved = resolve_cwd_path(root, logical_path)
    _verify_parent_chain(root, raw_target.parent)
    _reject_symlink_target(raw_target)
    _verify_parent_chain(root, resolved.parent)
    _reject_symlink_target(resolved)
    flags = os.O_RDONLY | _flag("O_NOFOLLOW")
    fd = os.open(resolved, flags)
    with os.fdopen(fd, "rb") as handle:
        return resolved, handle.read()


def safe_read_text(cwd: str | Path, logical_path: str, *, encoding: str = "utf-8") -> tuple[Path, str]:
    resolved, data = safe_read_bytes(cwd, logical_path)
    return resolved, data.decode(encoding)


def safe_atomic_write_text(
    cwd: str | Path,
    logical_path: str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    if os.name == "nt":
        raise PermissionError("cwd-bound atomic write is unsupported on Windows")
    root = resolve_cwd(cwd)
    raw_target = _logical_target(root, logical_path)
    resolved = resolve_cwd_path(root, logical_path)
    _verify_parent_chain(root, raw_target.parent)
    _reject_symlink_target(raw_target)
    parent = raw_target.parent
    _verify_parent_chain(root, parent)
    if not parent.exists():
        raise FileNotFoundError(f"Parent directory does not exist: {parent}")
    if not parent.is_dir():
        raise NotADirectoryError(f"Parent path is not a directory: {parent}")
    name = resolved.name
    parent_fd = os.open(parent, os.O_RDONLY | _flag("O_DIRECTORY") | _flag("O_NOFOLLOW"))
    temp_name = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    temp_created = False
    try:
        _reject_symlink_child(parent_fd, name)
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _flag("O_NOFOLLOW"),
            0o600,
            dir_fd=parent_fd,
        )
        temp_created = True
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _reject_symlink_child(parent_fd, name)
        os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        _reject_symlink_child(parent_fd, name)
    except Exception:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(parent_fd)
    return resolved


def _logical_target(root: Path, logical_path: str) -> Path:
    raw = Path(logical_path).expanduser()
    target = raw if raw.is_absolute() else root / raw
    return Path(os.path.normpath(str(target.absolute())))


def _verify_parent_chain(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"path escapes cwd: {parent}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise FileNotFoundError(f"Parent directory does not exist: {current}") from None
        if stat.S_ISLNK(mode):
            raise PermissionError(f"parent directory is a symlink: {current}")
        if not stat.S_ISDIR(mode):
            raise NotADirectoryError(f"parent path is not a directory: {current}")


def _reject_symlink_target(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise PermissionError(f"refusing to follow symlink: {path}")


def _reject_symlink_child(parent_fd: int, name: str) -> None:
    try:
        mode = os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise PermissionError(f"refusing to replace symlink: {name}")


def _flag(name: str) -> int:
    return int(getattr(os, name, 0))


__all__ = [
    "logical_mutation_key",
    "safe_atomic_write_text",
    "safe_read_bytes",
    "safe_read_text",
]
