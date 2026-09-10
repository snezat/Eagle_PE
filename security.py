from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from cryptography.fernet import Fernet, MultiFernet


def _write_private(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, value)
    finally:
        os.close(fd)


def _read_private(path: Path) -> bytes:
    if path.is_symlink():
        raise RuntimeError(f"Refusing unsafe secret-key path: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError(f"Refusing unsafe secret-key path: {path}")
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        chunks = []
        while chunk := os.read(fd, 4096):
            chunks.append(chunk)
        return b"".join(chunks).strip()
    finally:
        os.close(fd)


def load_or_create_bytes(path: Path, generator) -> bytes:
    try:
        return _read_private(path)
    except FileNotFoundError:
        value = generator()
        try:
            _write_private(path, value)
        except FileExistsError:
            return _read_private(path)
        return value


class FieldCipher:
    """Authenticated field encryption plus keyed lookup hashes."""

    def __init__(self, instance_path: Path):
        primary = load_or_create_bytes(instance_path / "master.key", Fernet.generate_key)
        old_keys = []
        old_path = instance_path / "old-master.keys"
        if old_path.exists():
            if old_path.is_symlink() or not old_path.is_file():
                raise RuntimeError(f"Refusing unsafe old-key path: {old_path}")
            if os.name != "nt":
                os.chmod(old_path, 0o600)
            old_keys = [line.strip() for line in old_path.read_bytes().splitlines() if line.strip()]
        self._fernet = MultiFernet([Fernet(primary), *[Fernet(key) for key in old_keys]])
        self._lookup_key = load_or_create_bytes(
            instance_path / "lookup.key", lambda: secrets.token_bytes(32).hex().encode()
        )

    def encrypt(self, value: object | None) -> bytes | None:
        if value is None:
            return None
        return self._fernet.encrypt(str(value).encode("utf-8"))

    def decrypt(self, value: bytes | str | None) -> str | None:
        if value is None:
            return None
        raw = value.encode() if isinstance(value, str) else value
        # The original one-time Node roster/max importer emitted otherwise-valid
        # URL-safe Fernet tokens without trailing Base64 padding. Accept those
        # legacy records while all new Python writes use canonical Fernet output.
        raw += b"=" * (-len(raw) % 4)
        return self._fernet.decrypt(raw).decode("utf-8")

    def lookup(self, value: str) -> str:
        normalized = value.strip().casefold().encode("utf-8")
        return hmac.new(self._lookup_key, normalized, hashlib.sha256).hexdigest()

    def digest(self, value: str) -> str:
        return hmac.new(self._lookup_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def new_secret_key(instance_path: Path) -> bytes:
    return load_or_create_bytes(instance_path / "flask-secret.key", lambda: secrets.token_bytes(64).hex().encode())


def ensure_setup_token(instance_path: Path) -> str:
    return load_or_create_bytes(
        instance_path / "setup-token", lambda: secrets.token_urlsafe(32).encode()
    ).decode()
