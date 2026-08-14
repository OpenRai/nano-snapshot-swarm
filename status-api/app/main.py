from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.models import PushRequest

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATUS_FILE = DATA_DIR / "status.json"
TORRENT_FILE = DATA_DIR / "torrent.bin"
TORRENTS_DIR = DATA_DIR / "torrents"
DEFAULT_PRODUCER_SIGNING_PUBKEY = "cdbc9284015e84c225f0e67b891606505a60cf1218b127ac1c1edb6444567e6b"
logger = logging.getLogger("status_api")


def _producer_signing_pubkey_from_env() -> str:
    if "AUTHORITY_PUBKEY" in os.environ:
        logger.warning("AUTHORITY_PUBKEY is ignored; use PRODUCER_SIGNING_PUBKEY.")
    return os.environ.get("PRODUCER_SIGNING_PUBKEY", DEFAULT_PRODUCER_SIGNING_PUBKEY)


PRODUCER_SIGNING_PUBKEY = _producer_signing_pubkey_from_env()
DHT_SALT = os.environ.get("DHT_SALT", "daily")
MAGNET_PEER_HOST = os.environ.get("MAGNET_PEER_HOST", "").strip()
MAGNET_PEER_PORT = os.environ.get("MAGNET_PEER_PORT", "6881").strip()
MAGNET_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
)
CANONICAL_TORRENT_NAME = "nano-ledger-snapshot.7z"
TEMPLATE_DIR = Path(__file__).parent / "templates"
JINJA = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

# Store current state in memory (reloaded from disk on startup)
_current_status: dict | None = None
_torrent_bytes: bytes = b""
_state_lock = threading.RLock()


def _named_torrent_path(info_hash: str, torrent_name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", info_hash):
        raise HTTPException(status_code=404, detail="Torrent not found")
    if torrent_name != Path(torrent_name).name:
        raise HTTPException(status_code=404, detail="Torrent not found")
    return TORRENTS_DIR / info_hash / f"{torrent_name}.torrent"


def _load_state() -> None:
    global _current_status, _torrent_bytes
    with _state_lock:
        if STATUS_FILE.exists():
            try:
                _current_status = json.loads(STATUS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                _current_status = None
        else:
            _current_status = None
        if TORRENT_FILE.exists():
            try:
                _torrent_bytes = TORRENT_FILE.read_bytes()
            except OSError:
                _torrent_bytes = b""
        else:
            _torrent_bytes = b""
        if _current_status and _torrent_bytes:
            old_pubkey_field = _current_status.pop("authority_pubkey", None)
            if old_pubkey_field is not None:
                _current_status["producer_signing_pubkey"] = PRODUCER_SIGNING_PUBKEY
            named_path = _named_torrent_path(
                _current_status["info_hash"], _current_status["torrent_name"]
            )
            if old_pubkey_field is not None or not named_path.exists():
                _save_state(_current_status, _torrent_bytes)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _load_state()
    yield


app = FastAPI(title="Nano Snapshot Status API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def _save_state(status: dict, torrent_bytes: bytes) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write for status.json
    with tempfile.NamedTemporaryFile(mode="w", dir=DATA_DIR, suffix=".tmp", delete=False) as f:
        json.dump(status, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.rename(f.name, STATUS_FILE)
    # Atomic write for torrent.bin
    with tempfile.NamedTemporaryFile(mode="wb", dir=DATA_DIR, suffix=".tmp", delete=False) as f:
        f.write(torrent_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.rename(f.name, TORRENT_FILE)
    named_path = _named_torrent_path(status["info_hash"], status["torrent_name"])
    named_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=named_path.parent, suffix=".tmp", delete=False
    ) as f:
        f.write(torrent_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.rename(f.name, named_path)


def _build_magnet(info_hash: str, torrent_name: str, info_hash_v1: str | None = None) -> str:
    """Compose the public hybrid magnet; see ../../docs/torrent-format.md."""
    params = [
        f"dn={quote(torrent_name)}",
    ]
    if info_hash_v1:
        params.insert(0, f"xt=urn:btih:{info_hash_v1}")
    params.insert(1 if info_hash_v1 else 0, f"xt=urn:btmh:1220{info_hash}")
    for tracker in MAGNET_TRACKERS:
        params.append(f"tr={quote(tracker, safe='')}")
    if MAGNET_PEER_HOST:
        params.append(f"x.pe={quote(f'{MAGNET_PEER_HOST}:{MAGNET_PEER_PORT}', safe='')}")
    return "magnet:?" + "&".join(params)


def canonical_push_payload(payload: PushRequest | dict) -> bytes:
    """Return the one signed JSON representation of a status push envelope."""
    if isinstance(payload, PushRequest):
        data = payload.model_dump(exclude={"signature"}, exclude_none=True)
    else:
        data = {
            key: value
            for key, value in payload.items()
            if key != "signature" and value is not None
        }
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_torrent(payload: PushRequest) -> bytes:
    try:
        return base64.b64decode(payload.torrent_file_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid torrent Base64") from exc


def verify_push(payload: PushRequest, producer_signing_pubkey_hex: str) -> bool:
    try:
        pubkey_bytes = bytes.fromhex(producer_signing_pubkey_hex)
        signature_bytes = bytes.fromhex(payload.signature)
        if len(pubkey_bytes) != 32 or len(signature_bytes) != 64:
            return False
        verify_key = VerifyKey(pubkey_bytes)
        verify_key.verify(canonical_push_payload(payload), signature_bytes)
        return True
    except (BadSignatureError, TypeError, ValueError):
        return False


def _same_snapshot_identity(payload: PushRequest, torrent_bytes: bytes) -> bool:
    """Return whether a signed refresh describes the currently stored torrent."""
    if _current_status is None:
        return False
    immutable_fields = (
        "sequence",
        "info_hash",
        "info_hash_v1",
        "torrent_name",
        "piece_size",
        "snapshot_size_bytes",
        "archive_listing",
    )
    return all(
        getattr(payload, field) == _current_status.get(field) for field in immutable_fields
    ) and torrent_bytes == _torrent_bytes


@app.post("/api/push")
def push(payload: PushRequest) -> JSONResponse:
    global _current_status, _torrent_bytes

    if not verify_push(payload, PRODUCER_SIGNING_PUBKEY):
        raise HTTPException(status_code=401, detail="Invalid signature")
    if payload.torrent_name != CANONICAL_TORRENT_NAME:
        raise HTTPException(status_code=422, detail="Unexpected torrent name")

    torrent_bytes = _decode_torrent(payload)
    payload_digest = hashlib.sha256(canonical_push_payload(payload)).hexdigest()
    with _state_lock:
        current_seq = _current_status.get("sequence", 0) if _current_status else 0
        if payload.sequence < current_seq:
            raise HTTPException(
                status_code=409,
                detail=f"Replay rejected (seq {payload.sequence} < {current_seq})",
            )

        if payload.sequence == current_seq and _current_status is not None:
            if (
                _current_status.get("payload_digest") == payload_digest
                and torrent_bytes == _torrent_bytes
            ):
                return JSONResponse({"ok": True, "sequence": payload.sequence})
            if not _same_snapshot_identity(payload, torrent_bytes):
                raise HTTPException(
                    status_code=409, detail="Conflicting payload for existing sequence"
                )

        magnet = _build_magnet(payload.info_hash, payload.torrent_name, payload.info_hash_v1)

        status = {
            "sequence": payload.sequence,
            "info_hash": payload.info_hash,
            "info_hash_v1": payload.info_hash_v1,
            "torrent_name": payload.torrent_name,
            "magnet": magnet,
            "torrent_download_url": "/api/torrent",
            "named_torrent_download_url": (
                f"/api/torrents/{payload.info_hash}/{quote(payload.torrent_name)}.torrent"
            ),
            "snapshot_size_bytes": payload.snapshot_size_bytes,
            "piece_size": payload.piece_size,
            "producer_signing_pubkey": PRODUCER_SIGNING_PUBKEY,
            "dht_salt": DHT_SALT,
            "verified": True,
            "timestamp": payload.timestamp,
            "archive_listing": payload.archive_listing,
            "payload_digest": payload_digest,
        }

        _save_state(status, torrent_bytes)
        _current_status = status
        _torrent_bytes = torrent_bytes

    return JSONResponse({"ok": True, "sequence": payload.sequence})


@app.get("/api/status")
def get_status() -> JSONResponse:
    with _state_lock:
        if _current_status is None:
            raise HTTPException(status_code=404, detail="No status available yet")
        headers = {"Cache-Control": "no-store"}
        return JSONResponse(_current_status, headers=headers)


def _render_fragment() -> str:
    with _state_lock:
        status = {**_current_status, "torrent_name": _current_status["torrent_name"] + ".torrent"}
        return JINJA.get_template("status_fragment.html").render(**status)


@app.get("/api/status-fragment")
def get_status_fragment() -> Response:
    with _state_lock:
        if _current_status is None:
            raise HTTPException(status_code=404, detail="No status available yet")
    rendered = _render_fragment()
    headers = {
        "Content-Type": "text/html",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
    }
    return Response(content=rendered, headers=headers)


@app.get("/api/torrent")
def get_torrent() -> Response:
    with _state_lock:
        if _current_status is None or not _torrent_bytes:
            raise HTTPException(status_code=404, detail="No torrent available yet")
        url = _current_status["named_torrent_download_url"] + f"?v={_current_status['info_hash']}"
        return RedirectResponse(url=url, status_code=307, headers={"Cache-Control": "no-store"})


@app.get("/api/torrents/{info_hash}/{torrent_name}.torrent")
def get_named_torrent(info_hash: str, torrent_name: str) -> Response:
    torrent_path = _named_torrent_path(info_hash, torrent_name)
    if not torrent_path.exists():
        raise HTTPException(status_code=404, detail="Torrent not found")
    headers = {
        "Content-Type": "application/x-bittorrent",
        "Content-Disposition": f'attachment; filename="{torrent_name}.torrent"',
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    return Response(content=torrent_path.read_bytes(), headers=headers)


@app.get("/api/latest.magnet")
def get_latest_magnet() -> Response:
    with _state_lock:
        if _current_status is None:
            raise HTTPException(status_code=404, detail="No status available yet")
        return Response(
            content=_current_status["magnet"],
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )


@app.get("/nano-snapshot-swarm.producer-signing-pubkey.txt")
def get_public_key() -> Response:
    """Serve the current producer signing public key as plain text."""
    return Response(
        content=f"{PRODUCER_SIGNING_PUBKEY}\n",
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/")
def index() -> Response:
    with _state_lock:
        if _current_status is None:
            html = JINJA.get_template("index.html").render(status_fragment="")
            return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

        fragment = _render_fragment()
        html = JINJA.get_template("index.html").render(status_fragment=fragment)
        return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> JSONResponse:
    with _state_lock:
        body = {
            "status": "ok",
            "sequence": _current_status.get("sequence", 0) if _current_status else 0,
            "updated_at": _current_status.get("timestamp", "") if _current_status else "",
        }
        return JSONResponse(body, headers={"Cache-Control": "no-store"})
