"""Offline encrypted recovery bundles. This utility is intentionally not an API route."""

from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..services.backups import create_compact_snapshot

# Stable file-format signature so existing encrypted recovery bundles remain readable.
MAGIC = b"FOURTHDOWN-RECOVERY-1\n"
ENVIRONMENT_KEYS = (
    "APP_ENV",
    "APP_SECRET",
    "CODEX_RUNNER_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "YAHOO_CLIENT_ID",
    "YAHOO_CLIENT_SECRET",
    "YAHOO_REDIRECT_URI",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "PUBLIC_BASE_URL",
    "OWNER_USERNAME",
    "AUTH_REQUIRED",
    "TIMEZONE",
)


def _key(password: str, salt: bytes) -> bytes:
    if len(password) < 12:
        raise ValueError("Use a recovery passphrase of at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode())


def _add_tree(archive: tarfile.TarFile, root: Path, prefix: str, excluded: set[str]) -> None:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Recovery inputs cannot contain symbolic links: {relative}")
        if path.is_file():
            archive.add(path, arcname=f"{prefix}/{relative.as_posix()}", recursive=False)


def create_bundle(
    data_dir: Path,
    codex_home: Path,
    output: Path,
    password: str,
    *,
    environment: dict[str, str] | None = None,
    environment_file: Path | None = None,
    database: Path | None = None,
) -> Path:
    if not data_dir.is_dir() or not codex_home.is_dir():
        raise ValueError("Both application data and Codex home directories must exist")
    configuration = {
        key: value for key, value in (environment or {}).items() if key in ENVIRONMENT_KEYS
    }
    if (
        configuration.get("APP_SECRET", "local-development-secret-change-me")
        == "local-development-secret-change-me"
        and not (data_dir / "master-secret").is_file()
        and environment_file is None
    ):
        raise ValueError("The recovery bundle needs the original APP_SECRET or local master-secret")
    database = database or data_dir / "football.db"
    if not database.is_file():
        raise ValueError("The application database does not exist")
    if output.exists():
        raise ValueError("Refusing to overwrite an existing recovery bundle")
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _key(password, salt)
    with tempfile.TemporaryDirectory(prefix="football-recovery-") as temporary:
        stage = Path(temporary)
        snapshot = stage / "football.db"
        create_compact_snapshot(database, snapshot)
        manifest = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "environment": configuration,
            "database": "data/football.db",
            "excluded": ["cache", "backups", "locks"],
        }
        archive_path = stage / "recovery.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            contents = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(contents), 0o600
            archive.addfile(info, io.BytesIO(contents))
            archive.add(snapshot, arcname="data/football.db")
            _add_tree(
                archive,
                data_dir,
                "data",
                {
                    "cache",
                    "backups",
                    "locks",
                    database.name,
                    f"{database.name}-wal",
                    f"{database.name}-shm",
                },
            )
            _add_tree(archive, codex_home, "codex-home", {".lock"})
            if environment_file is not None:
                contents = environment_file.read_bytes()
                info = tarfile.TarInfo("configuration.env")
                info.size, info.mode = len(contents), 0o600
                archive.addfile(info, io.BytesIO(contents))
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as destination, archive_path.open("rb") as source:
                encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
                header = MAGIC + salt + nonce
                encryptor.authenticate_additional_data(header)
                destination.write(header)
                while block := source.read(1024 * 1024):
                    destination.write(encryptor.update(block))
                destination.write(encryptor.finalize())
                destination.write(encryptor.tag)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return output


def restore_bundle(bundle: Path, output_dir: Path, password: str) -> Path:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Restore requires a new or empty destination directory")
    with tempfile.TemporaryDirectory(prefix="football-restore-") as temporary:
        stage = Path(temporary)
        archive_path = stage / "recovery.tar.gz"
        with bundle.open("rb") as source, archive_path.open("wb") as destination:
            header = source.read(len(MAGIC) + 28)
            if len(header) != len(MAGIC) + 28 or not header.startswith(MAGIC):
                raise ValueError("Unsupported recovery bundle")
            salt, nonce = header[len(MAGIC) : len(MAGIC) + 16], header[-12:]
            size = bundle.stat().st_size - len(header) - 16
            if size < 0:
                raise ValueError("Truncated recovery bundle")
            source.seek(-16, os.SEEK_END)
            tag = source.read(16)
            source.seek(len(header))
            decryptor = Cipher(
                algorithms.AES(_key(password, salt)), modes.GCM(nonce, tag)
            ).decryptor()
            decryptor.authenticate_additional_data(header)
            try:
                while size:
                    block = source.read(min(1024 * 1024, size))
                    if not block:
                        raise ValueError("Truncated recovery bundle")
                    size -= len(block)
                    destination.write(decryptor.update(block))
                destination.write(decryptor.finalize())
            except InvalidTag as exc:
                raise ValueError("Wrong passphrase or damaged recovery bundle") from exc
        extracted = stage / "verified"
        extracted.mkdir(mode=0o700)
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0]
                    not in {"data", "codex-home", "manifest.json", "configuration.env"}
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("Unsafe file in recovery bundle")
            archive.extractall(extracted, filter="data")
        manifest = json.loads((extracted / "manifest.json").read_text())
        if manifest.get("version") != 1:
            raise ValueError("Unsupported recovery manifest")
        with sqlite3.connect(
            f"{(extracted / 'data/football.db').as_uri()}?mode=ro", uri=True
        ) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Restored database failed its integrity check")
        (extracted / "codex-home").mkdir(exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir.chmod(0o700)
        for path in extracted.iterdir():
            shutil.move(str(path), output_dir / path.name)
        for path in output_dir.rglob("*"):
            # Keep executable hooks/binaries usable while removing all group/world access.
            path.chmod(0o700 if path.is_dir() or path.stat().st_mode & 0o111 else 0o600)
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["create", "restore"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--password-file",
        type=Path,
        help="Read a passphrase from a protected file instead of a terminal",
    )
    args = parser.parse_args()
    password = (
        args.password_file.read_text().rstrip("\r\n")
        if args.password_file
        else getpass.getpass("Recovery passphrase: ")
    )
    if args.operation == "create":
        if not args.data_dir or not args.codex_home:
            parser.error("create requires --data-dir and --codex-home")
        if not args.password_file and password != getpass.getpass("Confirm recovery passphrase: "):
            parser.error("Recovery passphrases do not match")
        result = create_bundle(
            args.data_dir,
            args.codex_home,
            args.bundle,
            password,
            environment=dict(os.environ),
            environment_file=args.environment_file,
            database=args.database,
        )
    else:
        if not args.output_dir:
            parser.error("restore requires --output-dir")
        result = restore_bundle(args.bundle, args.output_dir, password)
    print(f"Recovery {args.operation} completed: {result}")


if __name__ == "__main__":
    main()
