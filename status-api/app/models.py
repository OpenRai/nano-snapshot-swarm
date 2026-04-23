from __future__ import annotations

from pydantic import BaseModel


class PushRequest(BaseModel):
    sequence: int
    info_hash: str
    torrent_name: str
    web_seed_url: str
    piece_size: int
    snapshot_size_bytes: int
    timestamp: str  # ISO 8601
    torrent_file_b64: str  # base64-encoded .torrent
    signature: str  # hex Ed25519 signature


class StatusResponse(BaseModel):
    sequence: int
    info_hash: str
    torrent_name: str
    magnet: str
    web_seed_url: str
    torrent_download_url: str
    snapshot_size_bytes: int
    piece_size: int
    authority_pubkey: str
    dht_salt: str
    verified: bool
    timestamp: str
