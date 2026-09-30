"""Safe tar handling for sandbox inputs and outputs.

Everything that comes back from a sandbox is attacker-controlled: generated code can craft any tar stream it
likes (``../`` traversal, absolute paths, symlinks to ``/etc/passwd``, hard links, device nodes, sparse or
enormous members, millions of entries). :func:`safe_extract` therefore never uses ``TarFile.extract*``; it
reads member headers itself, accepts only regular files and directories, normalizes and contains every path,
writes file bodies with ``O_EXCL | O_NOFOLLOW`` under a fresh destination directory, and enforces byte and
entry budgets while streaming (the declared member size is never trusted alone).

:class:`TarBuilder` writes deterministic, uncompressed tars (fixed mtime/uid/gid, no links) used to stage
inputs into a sandbox.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

CHUNK_SIZE = 1024 * 1024
# Upper bound for tar framing overhead per member (header + PAX record + padding).
TAR_OVERHEAD_PER_MEMBER = 3 * 512 + 1024
# Names used by the sandbox protocol itself (completion markers) that inputs may not shadow.
RESERVED_NAMES = frozenset({".done", ".uploaded"})


class ArchiveError(Exception):
    """Base class for archive problems."""


class UnsafeArchiveMember(ArchiveError):
    """A member would escape the destination or is not a plain file/directory."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"unsafe archive member {name[:200]!r}: {reason}")
        self.name = name
        self.reason = reason


class ArchiveLimitExceeded(ArchiveError):
    """The archive exceeds a size or entry-count budget."""


@dataclass(frozen=True)
class ExtractLimits:
    max_total_bytes: int
    max_files: int
    max_member_bytes: int | None = None
    max_path_length: int = 1024
    max_depth: int = 32
    max_entries: int | None = None  # files + directories; default 4 × max_files + 64

    @property
    def entry_budget(self) -> int:
        return self.max_entries if self.max_entries is not None else self.max_files * 4 + 64


@dataclass(frozen=True)
class ExtractedFile:
    path: str  # normalized POSIX path relative to the destination (after ``strip_prefix``)
    size: int
    sha256: str
    local_path: Path


@dataclass(frozen=True)
class RejectedMember:
    name: str
    reason: str


@dataclass
class ExtractionReport:
    files: list[ExtractedFile] = field(default_factory=list)
    rejected: list[RejectedMember] = field(default_factory=list)
    total_bytes: int = 0

    def by_path(self) -> dict[str, ExtractedFile]:
        return {f.path: f for f in self.files}


# ---------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------
def normalize_member_path(
    name: str, *, strip_prefix: str | None = None, max_length: int = 1024, max_depth: int = 32
) -> PurePosixPath | None:
    """Normalize an archive member name to a safe relative path.

    Returns ``None`` for entries that are the ``strip_prefix`` directory itself (or ``.``). Raises
    :class:`UnsafeArchiveMember` for absolute paths, ``..`` components, NUL bytes, backslashes, overlong or
    overly deep paths, and names outside ``strip_prefix``.
    """
    if not isinstance(name, str) or not name:
        raise UnsafeArchiveMember(str(name), "empty name")
    if "\x00" in name:
        raise UnsafeArchiveMember(name, "NUL byte in name")
    if "\\" in name:
        raise UnsafeArchiveMember(name, "backslash in name")
    if len(name) > max_length:
        raise UnsafeArchiveMember(name, "name too long")
    if name.startswith("/"):
        raise UnsafeArchiveMember(name, "absolute path")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafeArchiveMember(name, "parent directory reference")
    if strip_prefix is not None:
        prefix_parts = [p for p in strip_prefix.split("/") if p not in ("", ".")]
        if parts[: len(prefix_parts)] != prefix_parts:
            raise UnsafeArchiveMember(name, f"outside of {strip_prefix!r}")
        parts = parts[len(prefix_parts) :]
    if not parts:
        return None
    if len(parts) > max_depth:
        raise UnsafeArchiveMember(name, "path too deep")
    return PurePosixPath(*parts)


def normalize_relative_path(path: str, *, allowed_roots: Iterable[str] | None = None) -> str:
    """Validate a caller-supplied relative path (e.g. an input mount point). Raises ``ValueError``."""
    try:
        normalized = normalize_member_path(path)
    except UnsafeArchiveMember as exc:
        raise ValueError(f"invalid path {path!r}: {exc.reason}") from exc
    if normalized is None:
        raise ValueError(f"invalid path {path!r}: empty")
    text = normalized.as_posix()
    if any(part in RESERVED_NAMES for part in normalized.parts):
        raise ValueError(f"invalid path {path!r}: reserved name")
    if allowed_roots is not None:
        roots = tuple(allowed_roots)
        if normalized.parts[0] not in roots:
            raise ValueError(f"invalid path {path!r}: must be under {' or '.join(r + '/' for r in roots)}")
    return text


# ---------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------
def _member_kind(member: tarfile.TarInfo) -> str | None:
    """'file' | 'dir' | None (anything else is unsafe)."""
    if member.issparse():
        return None
    if member.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.CONTTYPE):
        return "file"
    if member.type == tarfile.DIRTYPE:
        return "dir"
    return None


def _describe_type(member: tarfile.TarInfo) -> str:
    if member.issym():
        return "symbolic link"
    if member.islnk():
        return "hard link"
    if member.ischr() or member.isblk():
        return "device node"
    if member.isfifo():
        return "fifo"
    if member.issparse():
        return "sparse file"
    return f"unsupported member type {member.type!r}"


def _ensure_dir(root: Path, rel: PurePosixPath) -> Path:
    current = root
    for part in rel.parts:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise UnsafeArchiveMember(rel.as_posix(), "path conflicts with an existing file")
    return current


def safe_extract(
    fileobj: IO[bytes],
    dest: Path,
    limits: ExtractLimits,
    *,
    strip_prefix: str | None = None,
    strict: bool = True,
) -> ExtractionReport:
    """Extract a (optionally gzip-compressed) tar stream into ``dest`` safely.

    ``strict=True`` raises :class:`UnsafeArchiveMember` on the first unsafe member (inputs, code snapshots);
    ``strict=False`` skips unsafe members and reports them (sandbox outputs: the job's other files are still
    collected). Budget violations always raise :class:`ArchiveLimitExceeded`.
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    report = ExtractionReport()
    seen: set[str] = set()
    entries = 0
    try:
        tar = tarfile.open(fileobj=fileobj, mode="r|*")  # noqa: SIM115 - closed in finally
    except tarfile.TarError as exc:
        raise ArchiveError(f"not a valid tar archive: {exc}") from exc
    try:
        for member in _iter_members(tar):
            entries += 1
            if entries > limits.entry_budget:
                raise ArchiveLimitExceeded(f"archive has more than {limits.entry_budget} entries")
            try:
                rel = normalize_member_path(
                    member.name,
                    strip_prefix=strip_prefix,
                    max_length=limits.max_path_length,
                    max_depth=limits.max_depth,
                )
                kind = _member_kind(member)
                if kind is None:
                    raise UnsafeArchiveMember(member.name, _describe_type(member))
                if rel is None:
                    continue
                key = rel.as_posix()
                if key in seen:
                    raise UnsafeArchiveMember(member.name, "duplicate entry")
                if kind == "dir":
                    _ensure_dir(root, rel)
                    seen.add(key)
                    continue
                if len(report.files) + 1 > limits.max_files:
                    raise ArchiveLimitExceeded(f"archive has more than {limits.max_files} files")
                if member.size < 0:
                    raise UnsafeArchiveMember(member.name, "negative size")
                if limits.max_member_bytes is not None and member.size > limits.max_member_bytes:
                    raise ArchiveLimitExceeded(f"{key} exceeds the per-file limit of {limits.max_member_bytes} bytes")
                if report.total_bytes + member.size > limits.max_total_bytes:
                    raise ArchiveLimitExceeded(f"archive content exceeds {limits.max_total_bytes} bytes")
                parent = _ensure_dir(root, rel.parent) if rel.parent.parts else root
                target = parent / rel.name
                extracted = _write_member(tar, member, target, key, limits, report.total_bytes)
                seen.add(key)
                report.files.append(extracted)
                report.total_bytes += extracted.size
            except UnsafeArchiveMember as exc:
                if strict:
                    raise
                report.rejected.append(RejectedMember(name=member.name[:300], reason=exc.reason))
    except tarfile.TarError as exc:
        raise ArchiveError(f"corrupt tar archive: {exc}") from exc
    finally:
        tar.close()
    return report


def _iter_members(tar: tarfile.TarFile) -> Iterator[tarfile.TarInfo]:
    while True:
        member = tar.next()
        if member is None:
            return
        yield member


def _write_member(
    tar: tarfile.TarFile,
    member: tarfile.TarInfo,
    target: Path,
    key: str,
    limits: ExtractLimits,
    used: int,
) -> ExtractedFile:
    source = tar.extractfile(member)
    if source is None:  # pragma: no cover - regular members always have a body
        raise UnsafeArchiveMember(member.name, "unreadable member")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise UnsafeArchiveMember(member.name, "duplicate entry") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if used + size > limits.max_total_bytes:
                    raise ArchiveLimitExceeded(f"archive content exceeds {limits.max_total_bytes} bytes")
                if limits.max_member_bytes is not None and size > limits.max_member_bytes:
                    raise ArchiveLimitExceeded(f"{key} exceeds the per-file limit of {limits.max_member_bytes} bytes")
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return ExtractedFile(path=key, size=size, sha256=digest.hexdigest(), local_path=target)


# ---------------------------------------------------------------------------------------------
# Streaming / spooling
# ---------------------------------------------------------------------------------------------
class ChunkReader(io.RawIOBase):
    """A read-only file object over an iterator of byte chunks, aborting past ``max_bytes``.

    Lets :func:`safe_extract` consume an HTTP response body in a single pass without buffering it.
    """

    def __init__(self, chunks: Iterable[bytes], max_bytes: int) -> None:
        super().__init__()
        self._iter = iter(chunks)
        self._buffer = b""
        self._max = max_bytes
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        view = memoryview(b).cast("B")
        while not self._buffer:
            try:
                chunk = next(self._iter)
            except StopIteration:
                return 0
            if not chunk:
                continue
            self.consumed += len(chunk)
            if self.consumed > self._max:
                raise ArchiveLimitExceeded(f"stream exceeds {self._max} bytes")
            self._buffer = bytes(chunk)
        n = min(len(view), len(self._buffer))
        view[:n] = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return n


def stream_budget(limits: ExtractLimits) -> int:
    """Maximum raw tar bytes accepted for ``limits`` (content + per-entry framing + end blocks)."""
    return limits.max_total_bytes + limits.entry_budget * TAR_OVERHEAD_PER_MEMBER + 20 * 512


def copy_to_file(chunks: Iterable[bytes], target: Path, max_bytes: int) -> tuple[int, str]:
    """Stream ``chunks`` into a new file (exclusive create) → ``(size, sha256)``; aborts past ``max_bytes``."""
    digest = hashlib.sha256()
    size = 0
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ArchiveLimitExceeded(f"stream exceeds {max_bytes} bytes")
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


# ---------------------------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------------------------
class TarBuilder:
    """Deterministic tar writer (fixed metadata, regular files and directories only)."""

    def __init__(self, fileobj: IO[bytes], *, uid: int = 0, gid: int = 0, max_total_bytes: int | None = None) -> None:
        self._tar = tarfile.open(fileobj=fileobj, mode="w", format=tarfile.PAX_FORMAT)  # noqa: SIM115
        self._uid = uid
        self._gid = gid
        self._dirs: set[str] = set()
        self._files: set[str] = set()
        self._max_total = max_total_bytes
        self.total_bytes = 0

    def __enter__(self) -> TarBuilder:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._tar.close()

    def _info(self, path: str, *, kind: bytes, mode: int, uid: int | None, gid: int | None) -> tarfile.TarInfo:
        info = tarfile.TarInfo(path)
        info.type = kind
        info.mode = mode
        info.uid = self._uid if uid is None else uid
        info.gid = self._gid if gid is None else gid
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        return info

    def _check_path(self, path: str) -> str:
        normalized = normalize_member_path(path)
        if normalized is None:
            raise ValueError(f"invalid archive path {path!r}")
        return normalized.as_posix()

    def add_dir(self, path: str, *, mode: int = 0o755, uid: int | None = None, gid: int | None = None) -> None:
        key = self._check_path(path)
        parent = PurePosixPath(key).parent
        if parent.parts and parent.as_posix() not in self._dirs:
            self.add_dir(parent.as_posix(), mode=mode, uid=uid, gid=gid)
        if key in self._dirs:
            return
        if key in self._files:
            raise ValueError(f"{key} already added as a file")
        self._tar.addfile(self._info(key, kind=tarfile.DIRTYPE, mode=mode, uid=uid, gid=gid))
        self._dirs.add(key)

    def add_stream(
        self,
        path: str,
        fileobj: IO[bytes],
        size: int,
        *,
        mode: int = 0o444,
        dir_mode: int = 0o555,
        uid: int | None = None,
        gid: int | None = None,
    ) -> None:
        key = self._check_path(path)
        if key in self._files or key in self._dirs:
            raise ValueError(f"duplicate archive path {key!r}")
        if self._max_total is not None and self.total_bytes + size > self._max_total:
            raise ArchiveLimitExceeded(f"staged inputs exceed {self._max_total} bytes")
        parent = PurePosixPath(key).parent
        if parent.parts:
            self.add_dir(parent.as_posix(), mode=dir_mode, uid=uid, gid=gid)
        info = self._info(key, kind=tarfile.REGTYPE, mode=mode, uid=uid, gid=gid)
        info.size = size
        self._tar.addfile(info, fileobj)
        self._files.add(key)
        self.total_bytes += size

    def add_bytes(self, path: str, data: bytes, **kwargs: Any) -> None:
        self.add_stream(path, io.BytesIO(data), len(data), **kwargs)

    def add_file(self, path: str, local_path: Path, **kwargs: Any) -> None:
        size = local_path.stat().st_size
        with local_path.open("rb") as fh:
            self.add_stream(path, fh, size, **kwargs)

    def add_tree(self, prefix: str, root: Path, **kwargs: Any) -> None:
        """Add every regular file below ``root`` (symlinks are never followed or added)."""
        dir_mode = kwargs.get("dir_mode", 0o555)
        self.add_dir(prefix, mode=dir_mode, uid=kwargs.get("uid"), gid=kwargs.get("gid"))
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            rel_dir = base.relative_to(root)
            dirnames.sort()
            for name in sorted(filenames):
                local = base / name
                if local.is_symlink() or not local.is_file():
                    continue
                rel = (rel_dir / name).as_posix()
                self.add_file(f"{prefix}/{rel}", local, **kwargs)
