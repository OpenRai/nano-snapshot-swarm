from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.models import PushRequest

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATUS_FILE = DATA_DIR / "status.json"
TORRENT_FILE = DATA_DIR / "torrent.bin"
AUTHORITY_PUBKEY = os.environ.get(
    "AUTHORITY_PUBKEY",
    "cdbc9284015e84c225f0e67b891606505a60cf1218b127ac1c1edb6444567e6b",
)
DHT_SALT = os.environ.get("DHT_SALT", "daily")

app = FastAPI(title="Nano Snapshot Status API")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# Store current state in memory (reloaded from disk on startup)
_current_status: dict | None = None
_torrent_bytes: bytes = b""

TRACKERS = [
    "udp://router.bittorrent.com:6881",
    "udp://router.utorrent.com:6881",
    "udp://dht.transmissionbt.com:6881",
]


def _load_state() -> None:
    global _current_status, _torrent_bytes
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


def _build_magnet(info_hash: str, torrent_name: str) -> str:
    from urllib.parse import quote

    params = [
        f"xt=urn:btmh:1220{info_hash}",
        f"dn={quote(torrent_name)}",
    ]
    for tr in TRACKERS:
        params.append(f"tr={quote(tr)}")
    return "magnet:?" + "&".join(params)


def verify_push(payload: PushRequest, authority_pubkey_hex: str) -> bool:
    pubkey_bytes = bytes.fromhex(authority_pubkey_hex)
    verify_key = VerifyKey(pubkey_bytes)
    message = f"{payload.sequence}:{payload.info_hash}:{payload.timestamp}".encode("ascii")
    try:
        verify_key.verify(message, bytes.fromhex(payload.signature))
        return True
    except BadSignatureError:
        return False


@app.on_event("startup")
def startup() -> None:
    _load_state()


@app.post("/api/push")
def push(payload: PushRequest) -> JSONResponse:
    global _current_status, _torrent_bytes

    if not verify_push(payload, AUTHORITY_PUBKEY):
        raise HTTPException(status_code=401, detail="Invalid signature")

    current_seq = _current_status.get("sequence", 0) if _current_status else 0
    if payload.sequence < current_seq:
        raise HTTPException(
            status_code=409, detail=f"Replay rejected (seq {payload.sequence} < {current_seq})"
        )

    torrent_bytes = base64.b64decode(payload.torrent_file_b64)
    magnet = _build_magnet(payload.info_hash, payload.torrent_name)

    status = {
        "sequence": payload.sequence,
        "info_hash": payload.info_hash,
        "torrent_name": payload.torrent_name,
        "magnet": magnet,
        "web_seed_url": payload.web_seed_url,
        "torrent_download_url": "/api/torrent",
        "snapshot_size_bytes": payload.snapshot_size_bytes,
        "piece_size": payload.piece_size,
        "authority_pubkey": AUTHORITY_PUBKEY,
        "dht_salt": DHT_SALT,
        "verified": True,
        "timestamp": payload.timestamp,
    }

    _save_state(status, torrent_bytes)
    _current_status = status
    _torrent_bytes = torrent_bytes

    return JSONResponse({"ok": True, "sequence": payload.sequence})


@app.get("/api/status")
def get_status() -> JSONResponse:
    if _current_status is None:
        raise HTTPException(status_code=404, detail="No status available yet")
    headers = {"Cache-Control": "public, max-age=600"}
    return JSONResponse(_current_status, headers=headers)


@app.get("/api/status-fragment")
def get_status_fragment() -> Response:
    if _current_status is None:
        raise HTTPException(status_code=404, detail="No status available yet")
    html = (Path(__file__).parent / "templates" / "status_fragment.html").read_text()
    rendered = html.replace("{{ sequence }}", str(_current_status["sequence"]))
    rendered = rendered.replace("{{ info_hash }}", _current_status["info_hash"])
    rendered = rendered.replace("{{ timestamp }}", _current_status["timestamp"])
    rendered = rendered.replace("{{ magnet }}", _current_status["magnet"])
    rendered = rendered.replace("{{ web_seed_url }}", _current_status["web_seed_url"])
    rendered += f'<span id="_push-ts" data-ts="{_current_status["timestamp"]}" hidden></span>'
    headers = {
        "Content-Type": "text/html",
        "Cache-Control": "public, max-age=300",
        "Access-Control-Allow-Origin": "*",
    }
    return Response(content=rendered, headers=headers)


@app.get("/api/torrent")
def get_torrent() -> Response:
    if not _torrent_bytes:
        raise HTTPException(status_code=404, detail="No torrent available yet")
    headers = {
        "Content-Type": "application/x-bittorrent",
        "Content-Disposition": 'attachment; filename="nano-ledger-snapshot.7z.torrent"',
        "Cache-Control": "public, max-age=3600, immutable",
    }
    return Response(content=_torrent_bytes, headers=headers)


@app.get("/")
def index() -> Response:
    template_path = Path(__file__).parent / "templates" / "index.html"
    html = template_path.read_text()
    if _current_status is None:
        return HTMLResponse(content=html, headers={"Cache-Control": "public, max-age=300"})

    fragment_path = Path(__file__).parent / "templates" / "status_fragment.html"
    fragment = fragment_path.read_text()
    fragment = fragment.replace("{{ sequence }}", str(_current_status["sequence"]))
    fragment = fragment.replace("{{ info_hash }}", _current_status["info_hash"])
    fragment = fragment.replace("{{ timestamp }}", _current_status["timestamp"])
    fragment = fragment.replace("{{ web_seed_url }}", _current_status["web_seed_url"])
    fragment += f'<span id="_push-ts" data-ts="{_current_status["timestamp"]}" hidden></span>'

    html = html.replace("{{ status_fragment }}", fragment)
    return HTMLResponse(content=html, headers={"Cache-Control": "public, max-age=300"})


@app.get("/health")
def health() -> JSONResponse:
    body = {
        "status": "ok",
        "sequence": _current_status.get("sequence", 0) if _current_status else 0,
        "updated_at": _current_status.get("timestamp", "") if _current_status else "",
    }
    return JSONResponse(body)
