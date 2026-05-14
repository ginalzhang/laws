from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile

DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PETITION_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif"}
VOTER_ROLL_SUFFIXES = {".csv", ".txt"}


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    suffix: str
    data: bytes


def max_upload_bytes() -> int:
    raw = os.getenv("MAX_UPLOAD_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError("MAX_UPLOAD_BYTES must be an integer") from e


async def read_validated_upload(
    upload: UploadFile,
    allowed_suffixes: set[str],
    default_filename: str,
    max_bytes: int | None = None,
) -> ValidatedUpload:
    filename = upload.filename or default_filename
    suffix = Path(filename).suffix.lower()
    if not suffix:
        suffix = Path(default_filename).suffix.lower()
        filename = f"{filename}{suffix}"
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'}. Allowed: {allowed}")

    limit = max_bytes if max_bytes is not None else max_upload_bytes()
    data = await upload.read(limit + 1)
    if not data:
        raise HTTPException(400, "Uploaded file is empty")
    if len(data) > limit:
        raise HTTPException(413, f"Uploaded file is too large. Limit is {limit} bytes")

    return ValidatedUpload(filename=filename, suffix=suffix, data=data)
