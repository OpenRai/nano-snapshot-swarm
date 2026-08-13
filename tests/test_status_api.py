from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "status-api"))

from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_status_api_state(tmp_path, monkeypatch):
    import app.main as main_module

    data_dir = tmp_path / "status-api-data"
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(main_module, "STATUS_FILE", data_dir / "status.json")
    monkeypatch.setattr(main_module, "TORRENT_FILE", data_dir / "torrent.bin")
    monkeypatch.setattr(main_module, "TORRENTS_DIR", data_dir / "torrents")
    main_module._current_status = None
    main_module._torrent_bytes = b""
    yield
    main_module._current_status = None
    main_module._torrent_bytes = b""


@pytest.fixture
def sample_push_payload():
    """Return a valid push payload with signature pre-computed."""
    from nacl.signing import SigningKey

    from producer.push_status import sign_push

    # Use a known seed for deterministic tests
    seed = bytes.fromhex("e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d")
    signing_key = SigningKey(seed)
    pubkey_hex = signing_key.verify_key.encode().hex()

    sequence = 42
    info_hash = "ab" * 32
    timestamp = "2026-04-23T00:00:00Z"
    import base64

    payload = {
        "sequence": sequence,
        "info_hash": info_hash,
        "info_hash_v1": "cd" * 20,
        "torrent_name": "nano-ledger-snapshot.7z",
        "piece_size": 33554432,
        "snapshot_size_bytes": 64320000000,
        "timestamp": timestamp,
        "torrent_file_b64": base64.b64encode(b"fake-torrent-data").decode("ascii"),
        "archive_listing": "--\n2026-04-23 00:00:00 ....A 12 data.ldb",
    }
    payload["signature"] = sign_push(signing_key._signing_key.hex(), payload)
    return payload, pubkey_hex


class TestPush:
    def test_push_valid_signature(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        # Temporarily override the authority pubkey
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            assert resp.json()["sequence"] == 42
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_push_invalid_signature(self, client, sample_push_payload):
        payload, _ = sample_push_payload
        payload["signature"] = "00" * 64
        resp = client.post("/api/push", json=payload)
        assert resp.status_code == 401

    def test_push_rejects_noncanonical_torrent_name(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            payload["torrent_name"] = "upstream-snapshot.7z"
            from producer.push_status import sign_push

            payload["signature"] = sign_push(
                bytes.fromhex(
                    "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d"
                ).hex(),
                payload,
            )
            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 422
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_push_replay_rejected(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            # First push at seq 42
            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 200

            # Second push at seq 41 should be rejected
            payload["sequence"] = 41
            from producer.push_status import sign_push

            payload["signature"] = sign_push(
                "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
                payload,
            )

            resp = client.post("/api/push", json=payload)
            assert resp.status_code == 409
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("sequence", 43),
            ("info_hash", "ef" * 32),
            ("info_hash_v1", "12" * 20),
            ("torrent_name", "upstream-snapshot.7z"),
            ("piece_size", 65536),
            ("snapshot_size_bytes", 12),
            ("timestamp", "2026-04-24T00:00:00Z"),
            ("torrent_file_b64", "bW9kaWZpZWQ="),
            ("archive_listing", "changed"),
        ],
    )
    def test_mutating_any_signed_field_invalidates_signature(
        self, client, sample_push_payload, field, value
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            payload[field] = value
            response = client.post("/api/push", json=payload)
            assert response.status_code == 401
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_equal_sequence_identical_retry_is_idempotent(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            response = client.post("/api/push", json=payload)
            assert response.status_code == 200
            assert response.json() == {"ok": True, "sequence": payload["sequence"]}
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_equal_sequence_different_signed_payload_is_rejected(
        self, client, sample_push_payload
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        from producer.push_status import sign_push

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            changed = {**payload, "snapshot_size_bytes": payload["snapshot_size_bytes"] + 1}
            changed["signature"] = sign_push(
                "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
                changed,
            )
            assert client.post("/api/push", json=changed).status_code == 409
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_equal_sequence_signed_timestamp_refresh_is_accepted(
        self, client, sample_push_payload
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        from producer.push_status import sign_push

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            refreshed = {**payload, "timestamp": "2026-04-23T01:00:00Z"}
            refreshed["signature"] = sign_push(
                "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
                refreshed,
            )

            response = client.post("/api/push", json=refreshed)

            assert response.status_code == 200
            assert client.get("/api/status").json()["timestamp"] == refreshed["timestamp"]
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_equal_sequence_timestamp_refresh_survives_legacy_saved_state(
        self, client, sample_push_payload
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        from producer.push_status import sign_push

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            main_module._current_status.pop("payload_digest")
            refreshed = {**payload, "timestamp": "2026-04-23T01:00:00Z"}
            refreshed["signature"] = sign_push(
                "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
                refreshed,
            )

            assert client.post("/api/push", json=refreshed).status_code == 200
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_equal_sequence_modified_torrent_bytes_are_rejected(
        self, client, sample_push_payload
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        from producer.push_status import sign_push

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            changed = {**payload, "torrent_file_b64": "bW9kaWZpZWQtdG9ycmVudA=="}
            changed["signature"] = sign_push(
                "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
                changed,
            )
            assert client.post("/api/push", json=changed).status_code == 409
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_malformed_public_key_is_a_controlled_4xx(self, client, sample_push_payload):
        payload, _ = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = "not-a-key"
        try:
            assert client.post("/api/push", json=payload).status_code == 401
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_equal_sequence_identical_retry_survives_reload(
        self, client, sample_push_payload
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            main_module._current_status = None
            main_module._torrent_bytes = b""
            main_module._load_state()
            assert client.post("/api/push", json=payload).status_code == 200
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_malformed_signature_and_base64_are_4xx(self, client, sample_push_payload):
        payload, _ = sample_push_payload
        malformed_signature = {**payload, "signature": "not-a-signature"}
        assert client.post("/api/push", json=malformed_signature).status_code == 422
        malformed_base64 = {**payload, "torrent_file_b64": "%%%"}
        assert client.post("/api/push", json=malformed_base64).status_code == 422


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

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["sequence"] == 42
            assert data["info_hash"] == payload["info_hash"]
            assert data["info_hash_v1"] == payload["info_hash_v1"]
            assert data["producer_signing_pubkey"] == pubkey_hex
            assert "authority_pubkey" not in data
            assert data["verified"] is True
            assert "magnet" in data
            assert resp.headers["cache-control"] == "no-store"
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_magnet_includes_configured_peer_hint(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        original_host = main_module.MAGNET_PEER_HOST
        original_port = main_module.MAGNET_PEER_PORT
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        main_module.MAGNET_PEER_HOST = "bandwidth-martyr.openrai.org"
        main_module.MAGNET_PEER_PORT = "6881"
        try:
            client.post("/api/push", json=payload)
            magnet = client.get("/api/status").json()["magnet"]
            assert f"xt=urn:btih:{payload['info_hash_v1']}" in magnet
            assert f"xt=urn:btmh:1220{payload['info_hash']}" in magnet
            assert "x.pe=bandwidth-martyr.openrai.org%3A6881" in magnet
            assert "tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce" in magnet
            assert "tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce" in magnet
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module.MAGNET_PEER_HOST = original_host
            main_module.MAGNET_PEER_PORT = original_port
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_torrent_redirects_to_named_immutable_content(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            redirect = client.get("/api/torrent", follow_redirects=False)
            expected_path = (
                f"/api/torrents/{payload['info_hash']}/{payload['torrent_name']}.torrent"
            )
            assert redirect.status_code == 307
            assert redirect.headers["cache-control"] == "no-store"
            assert redirect.headers["location"].startswith(expected_path)

            resp = client.get(redirect.headers["location"])
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/x-bittorrent"
            assert resp.headers["content-disposition"] == (
                'attachment; filename="nano-ledger-snapshot.7z.torrent"'
            )
            assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert resp.content == b"fake-torrent-data"
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_latest_magnet_is_raw_non_cacheable_text(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/latest.magnet")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/plain")
            assert resp.headers["cache-control"] == "no-store"
            assert resp.text.startswith("magnet:?")
            assert "ws=" not in resp.text
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_public_key_endpoint_returns_plain_text_key(self, client):
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = "ab" * 32
        try:
            resp = client.get("/nano-snapshot-swarm.producer-signing-pubkey.txt")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/plain")
            assert resp.headers["cache-control"] == "public, max-age=3600"
            assert resp.text == ("ab" * 32) + "\n"
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey

    def test_old_authority_pubkey_environment_is_ignored(self, monkeypatch, caplog):
        import app.main as main_module

        monkeypatch.setenv("AUTHORITY_PUBKEY", "00" * 32)
        monkeypatch.setenv("PRODUCER_SIGNING_PUBKEY", "ab" * 32)

        with caplog.at_level("WARNING"):
            assert main_module._producer_signing_pubkey_from_env() == "ab" * 32

        assert "AUTHORITY_PUBKEY is ignored; use PRODUCER_SIGNING_PUBKEY." in caplog.text

    def test_startup_migrates_persisted_authority_pubkey(self, client):
        import app.main as main_module

        status = {
            "sequence": 42,
            "info_hash": "ab" * 32,
            "torrent_name": "nano-ledger-snapshot.7z",
            "authority_pubkey": "00" * 32,
        }
        main_module._save_state(status, b"fake-torrent-data")
        main_module._current_status = None
        main_module._torrent_bytes = b""

        main_module._load_state()

        assert main_module._current_status is not None
        assert "authority_pubkey" not in main_module._current_status
        assert (
            main_module._current_status["producer_signing_pubkey"]
            == main_module.PRODUCER_SIGNING_PUBKEY
        )

    def test_health_ok_before_push(self, client):
        import app.main as main_module

        main_module._current_status = None
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_status_fragment_cors(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/status-fragment")
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "*"
            assert resp.headers["content-type"] == "text/html"
            assert resp.headers["cache-control"] == "no-store"
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_status_fragment_renders_truncated_info_hash(self, client, sample_push_payload):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module
        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            client.post("/api/push", json=payload)
            resp = client.get("/api/status-fragment")
            assert resp.status_code == 200
            # payload['info_hash'] is 'ab' * 32, so first 16 chars is 'abababababababab'
            expected_short_hash = payload["info_hash"][:16]
            assert expected_short_hash in resp.text
            # It should not contain the full info_hash as a literal {{ info_hash[:16] }}
            assert "{{ info_hash[:16] }}" not in resp.text
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_status_fragment_escapes_archive_listing_and_attributes(
        self, client, sample_push_payload
    ):
        payload, pubkey_hex = sample_push_payload
        import app.main as main_module

        from producer.push_status import sign_push

        payload["archive_listing"] = '<script>alert("x")</script> & details'
        payload["signature"] = sign_push(
            "e06d3183d14159228433ed599221b80bd0a5ce8352e4bdf0262f76786ef1c74d",
            payload,
        )
        original_pubkey = main_module.PRODUCER_SIGNING_PUBKEY
        main_module.PRODUCER_SIGNING_PUBKEY = pubkey_hex
        try:
            assert client.post("/api/push", json=payload).status_code == 200
            response = client.get("/api/status-fragment")
            assert response.status_code == 200
            assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt; &amp; details" in response.text
            assert "<script>alert" not in response.text
            assert 'data-ts="2026-04-23T00:00:00Z"' in response.text
        finally:
            main_module.PRODUCER_SIGNING_PUBKEY = original_pubkey
            main_module._current_status = None
            main_module._torrent_bytes = b""

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert resp.headers["cache-control"] == "no-store"

    def test_health_is_not_cached(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"

    def test_index_has_direct_leech_options_and_compose_seed(self, client):
        response = client.get("/")
        assert response.status_code == 200

        one_shot = response.text.split('<section id="one-shot">', 1)[1].split(
            '<section id="seed"', 1
        )[0]
        seed = response.text.split('<section id="seed"', 1)[1].split(
            '<section id="about">', 1
        )[0]

        assert 'data-tab="docker"' in one_shot
        assert 'data-tab="podman"' in one_shot
        assert 'data-tab="compose"' not in one_shot
        assert "./nano-data:/data" in one_shot
        assert 'data-tab="compose"' in seed
        assert "./nano-data:/data" in seed
        assert "nano-data:/data" not in seed.replace("./nano-data:/data", "")
        assert 'role="tablist"' in one_shot
        assert 'role="tab"' in one_shot
        assert 'aria-selected="true"' in one_shot
        assert 'role="tabpanel"' in one_shot
        assert 'aria-controls="one-shot-panel-docker"' in one_shot
        assert 'hidden data-group="one-shot" data-id="podman"' in one_shot
        assert (
            'href="/nano-snapshot-swarm.producer-signing-pubkey.txt">an Ed25519 key</a>'
            in response.text
        )
        assert 'href="https://openrai.org/">The OpenRai Initiative</a>' in response.text
