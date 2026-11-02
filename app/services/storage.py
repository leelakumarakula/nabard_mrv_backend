"""Private evidence storage. Local filesystem adapter; swap for an S3-compatible adapter with the same interface."""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError

EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "application/pdf": {".pdf"},
    "text/csv": {".csv"},
}
SIGNATURES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "application/pdf": [b"%PDF-"],
}


def validate_upload(filename: str, mime_type: str, content: bytes, allowed_types: list[str]) -> None:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if not content:
        raise AppError(422, "empty_file", "The uploaded file is empty")
    if len(content) > max_bytes:
        raise AppError(413, "payload_too_large", f"File exceeds the {get_settings().max_upload_mb} MB limit")
    if mime_type not in allowed_types or mime_type not in EXTENSIONS:
        raise AppError(415, "unsupported_media_type", f"File type '{mime_type}' is not accepted")
    if Path(filename).suffix.lower() not in EXTENSIONS[mime_type]:
        raise AppError(415, "extension_mismatch", "File extension does not match its declared type")
    signatures = SIGNATURES.get(mime_type)
    if signatures and not any(content.startswith(sig) for sig in signatures):
        raise AppError(415, "content_mismatch", "File content does not match its declared type")
    if mime_type == "text/csv":
        try:
            content[:4096].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(415, "content_mismatch", "CSV files must be UTF-8 text") from exc


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_path

    def save(self, content: bytes, filename: str) -> str:
        now = datetime.now(UTC)
        key = f"evidence/{now:%Y/%m}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise AppError(400, "invalid_key", "Invalid storage key")
        return path

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()


def get_storage() -> LocalStorage:
    return LocalStorage()
