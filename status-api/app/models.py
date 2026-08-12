from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class PushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: StrictInt = Field(ge=0)
    info_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    info_hash_v1: str | None = None
    torrent_name: str
    piece_size: StrictInt = Field(gt=0)
    snapshot_size_bytes: StrictInt = Field(gt=0)
    timestamp: str  # ISO 8601
    torrent_file_b64: str  # base64-encoded .torrent
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")  # hex Ed25519 signature
    archive_listing: str | None = None

    @field_validator("info_hash_v1")
    @classmethod
    def validate_info_hash_v1(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("info_hash_v1 must be a 20-byte lowercase hex hash")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be ISO 8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("torrent_file_b64")
    @classmethod
    def validate_torrent_file_b64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("torrent_file_b64 must be valid Base64") from exc
        if not decoded:
            raise ValueError("torrent_file_b64 must not be empty")
        return value


class StatusResponse(BaseModel):
    sequence: int
    info_hash: str
    info_hash_v1: str | None = None
    torrent_name: str
    magnet: str
    torrent_download_url: str
    snapshot_size_bytes: int
    piece_size: int
    producer_signing_pubkey: str
    dht_salt: str
    verified: bool
    timestamp: str
