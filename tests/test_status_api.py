from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "status-api"))

pytest.importorskip("fastapi")

from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_push_payload():
    """Return a valid push payload with signature pre-computed."""
    from nacl.signing import SigningKey

    # Use a known seed for deterministic tests
    seed = bytes.fromhex("e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d")
    signing_key = SigningKey(seed)
    pubkey_hex = signing_key.verify_key.encode().hex()

    sequence = 42
    info_hash = "ab" * 32
    timestamp = "2026-04-23T00:00:00Z"
    message = f"{sequence}:{info_hash}:{timestamp}".encode("ascii")
    signature = signing_key.sign(message).signature.hex()

    import base64

    return {
        "sequence": sequence,
        "info_hash": info_hash,
        "torrent_name": "nano-ledger-snapshot.7z",
        "web_seed_url": "https://example.com/snapshots/latest/nano-ledger-snapshot.7z",
        "piece_size": 33554432,
        "snapshot_size_bytes": 64320000000,
        "timestamp": timestamp,
        "torrent_file_b64": base64.b64encode(b"fake-torrent-data").decode("ascii"),
        "signature": signature,
    }, pubkey_hex


class TestPush:
    def test_push_valid_signature(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        # Temporarily override the authority pubkey
        import app.main as main_module

        original_pubkey = main_module.AUTHORITY_PUBKEY
        main_module.AUTHORITY_PUBKEY = pubkey_hex
        try:
            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            assert resp.json()["sequence"] == 42
        finally:
            main_module.AUTHORITY_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_push_invalid_signature(self, client, sample_push_payload):
        payload, _ = sample_push_payload
        payload["signature"] = "00" * 64
        resp = client.post("/api/push", json=payload)
        assert resp.status_code == 401

    def test_push_replay_rejected(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.AUTHORITY_PUBKEY
        main_module.AUTHORITY_PUBKEY = pubkey_hex
        try:
            # First push at seq 42
            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 200

            # Second push at seq 41 should be rejected
            payload["sequence"] = 41
            message = f"41:{payload['info_hash']}:{payload['timestamp']}".encode("ascii")
            from nacl.signing import SigningKey

            seed = bytes.fromhex("e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d")
            signing_key = SigningKey(seed)
            payload["signature"] = signing_key.sign(message).signature.hex()

            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 409
        finally:
            main_module.AUTHORITY_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""


class TestGetEndpoints:
    def test_status_404_before_push(self, client):
        # Ensure clean state
        import app.main as main_module

        main_module._current_status = None
        resp = client.get("/api/status")
        assert resp.status_code == 404

    def test_torrent_404_before_push(self, client):
        import app.main as main_module

        main_module._torrent_bytes = b""
        resp = client.get("/api/torrent")
        assert resp.status_code == 404

    def test_status_returns_json_after_push(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.AUTHORITY_PUBKEY
        main_module.AUTHORITY_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["sequence"] == 42
            assert data["info_hash"] == payload["info_hash"]
            assert data["verified"] is True
            assert "magnet" in data
        finally:
            main_module.AUTHORITY_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_torrent_content_type(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.AUTHORITY_PUBKEY
        main_module.AUTHORITY_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/torrent")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/x-bittorrent"
            assert resp.content == b"fake-torrent-data"
        finally:
            main_module.AUTHORITY_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_health_ok_before_push(self, client):
        import app.main as main_module

        main_module._current_status = None
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_status_fragment_cors(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.AUTHORITY_PUBKEY
        main_module.AUTHORITY_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/status-fragment")
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "*"
            assert resp.headers["content-type"] == "text/html"
        finally:
            main_module.AUTHORITY_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
