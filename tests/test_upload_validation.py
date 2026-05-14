from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from petition_verifier.upload_validation import PETITION_SUFFIXES, read_validated_upload


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


@pytest.mark.asyncio
async def test_read_validated_upload_accepts_allowed_suffix():
    result = await read_validated_upload(
        _upload("packet.pdf", b"%PDF-1.4"),
        PETITION_SUFFIXES,
        "packet.pdf",
    )

    assert result.filename == "packet.pdf"
    assert result.suffix == ".pdf"
    assert result.data == b"%PDF-1.4"


@pytest.mark.asyncio
async def test_read_validated_upload_rejects_empty_file():
    with pytest.raises(HTTPException) as exc:
        await read_validated_upload(
            _upload("packet.pdf", b""),
            PETITION_SUFFIXES,
            "packet.pdf",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_read_validated_upload_rejects_disallowed_suffix():
    with pytest.raises(HTTPException) as exc:
        await read_validated_upload(
            _upload("packet.exe", b"data"),
            PETITION_SUFFIXES,
            "packet.pdf",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_read_validated_upload_rejects_oversized_file():
    with pytest.raises(HTTPException) as exc:
        await read_validated_upload(
            _upload("packet.pdf", b"123456"),
            PETITION_SUFFIXES,
            "packet.pdf",
            max_bytes=5,
        )

    assert exc.value.status_code == 413
