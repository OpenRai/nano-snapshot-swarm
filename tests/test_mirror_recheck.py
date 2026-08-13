from __future__ import annotations

import inspect
import sys
from types import SimpleNamespace

from producer.retention import SNAPSHOT_NAME


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def pause_torrent(self, info_hash: str) -> None:
        self.calls.append(("pause", info_hash))

    def remove_torrent(self, info_hash: str) -> None:
        self.calls.append(("remove", info_hash))

    def ensure_torrent(self, **kwargs) -> None:
        self.calls.append(("ensure", kwargs))

    def force_recheck(self, info_hash: str) -> None:
        self.calls.append(("recheck", info_hash))

    def resume_torrent(self, info_hash: str) -> None:
        self.calls.append(("resume", info_hash))

    def connect_peer(self, info_hash: str, host: str, port: int) -> None:
        self.calls.append(("connect", info_hash, host, port))

    def torrent_metadata(self, info_hash: str):
        return None


def test_replacement_waits_for_metadata_recheck_before_resuming(tmp_path) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import TorrentMetadataSnapshot
    from mirror.reconcile import DesiredSnapshot, ReconcileDecision
    from mirror.watcher import MirrorWatcher

    watcher = MirrorWatcher(producer_signing_pubkey_hex="ab" * 32, data_dir=str(tmp_path))
    session = FakeSession()
    watcher.session = session
    watcher._active_info_hash = "cd" * 32
    target = DesiredSnapshot(seq=2, info_hash="ef" * 32)

    watcher._apply_reconcile_decision(ReconcileDecision(action="replace", target=target))

    assert session.calls == [
        ("pause", "cd" * 32),
        ("remove", "cd" * 32),
        ("ensure", {"info_hash": "ef" * 32, "save_path": str(tmp_path), "paused": True}),
    ]

    watcher._apply_metadata(TorrentMetadataSnapshot(name=SNAPSHOT_NAME))
    watcher._begin_recheck_after_metadata(target.info_hash)
    watcher._resume_after_recheck(target.info_hash, "checking_files")
    watcher._resume_after_recheck(target.info_hash, "downloading")

    assert session.calls[-2:] == [("recheck", "ef" * 32), ("resume", "ef" * 32)]


def test_higher_dht_sequence_for_same_torrent_continues_transfer(tmp_path, caplog) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.dht_discovery import DHTDiscoveryResult
    from mirror.watcher import MirrorWatcher

    info_hash = "ab" * 32
    watcher = MirrorWatcher(producer_signing_pubkey_hex="cd" * 32, data_dir=str(tmp_path))
    watcher._reconcile_to_desired = lambda: None

    first = DHTDiscoveryResult(info_hash, 10, b"", True)
    refresh = DHTDiscoveryResult(info_hash, 11, b"", True)
    watcher._set_desired_snapshot(first)

    with caplog.at_level("INFO", logger="mirror.watcher"):
        watcher._set_desired_snapshot(refresh)

    assert watcher.state.last_seq == 11
    assert watcher.state.last_info_hash == info_hash
    assert "sequence advanced from 10 to 11 for the current torrent" in caplog.text
    assert "New snapshot detected" not in caplog.text


def test_swarm_mode_keeps_monitoring_after_seeding(tmp_path, monkeypatch) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    import mirror.watcher as watcher_module

    class SeedingSession:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def torrent_metadata(self, info_hash: str):
            return None

        def connect_peer(self, info_hash: str, host: str, port: int) -> None:
            self.calls.append((info_hash, host, port))

        def torrent_status(self, info_hash: str):
            from mirror.libtorrent_session import TorrentStatusSnapshot

            return TorrentStatusSnapshot(
                progress=1.0,
                state="seeding",
                num_peers=0,
                download_rate=0,
                upload_rate=0,
                is_seeding=True,
            )

    watcher = watcher_module.MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
        seed_peers=[("seed.example", 6881)],
    )
    session = SeedingSession()
    watcher.session = session
    watcher._active_info_hash = "cd" * 32
    watcher._running = True

    polls = 0

    def stop_after_twelve_polls(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 12:
            watcher._running = False

    monkeypatch.setattr(watcher_module.time, "sleep", stop_after_twelve_polls)

    watcher._monitor_active_torrent_loop()

    assert watcher._stop_reason is None
    assert session.calls == []


def test_swarm_mode_logs_seeding_heartbeat_every_five_minutes(
    tmp_path, monkeypatch, caplog
) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    import mirror.watcher as watcher_module

    class SeedingSession:
        def torrent_metadata(self, info_hash: str):
            return None

        def torrent_status(self, info_hash: str):
            from mirror.libtorrent_session import TorrentStatusSnapshot

            return TorrentStatusSnapshot(
                progress=1.0,
                state="seeding",
                num_peers=2,
                num_connections=2,
                download_rate=0,
                upload_rate=2048,
                is_seeding=True,
                total_upload=3 * 1024**2,
            )

    watcher = watcher_module.MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
    )
    watcher.session = SeedingSession()
    watcher._active_info_hash = "cd" * 32
    watcher._running = True

    now = 0.0

    def monotonic() -> float:
        nonlocal now
        current = now
        now += 5.0
        return current

    polls = 0

    def stop_after_62_polls(_seconds: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 62:
            watcher._running = False

    monkeypatch.setattr(watcher_module.time, "monotonic", monotonic)
    monkeypatch.setattr(watcher_module.time, "sleep", stop_after_62_polls)

    with caplog.at_level("INFO", logger="mirror.watcher"):
        watcher._monitor_active_torrent_loop()

    heartbeats = [line for line in caplog.messages if line.startswith("Seeding |")]
    assert heartbeats == [
        "Seeding | Peers: 2 (Seeds: 0) | Connections: 2 | UL: 2.0 KB/s | Total UL: 3.0 MiB"
    ]


def test_connected_seed_is_shown_and_does_not_trigger_no_peer_warning(
    tmp_path, caplog
) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import TorrentStatusSnapshot
    from mirror.watcher import MirrorWatcher

    watcher = MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
    )
    status = TorrentStatusSnapshot(
        progress=0.4,
        state="downloading",
        num_peers=0,
        num_seeds=1,
        num_connections=1,
        download_rate=1024,
        upload_rate=0,
        is_seeding=False,
    )

    with caplog.at_level("INFO", logger="mirror.watcher"):
        watcher._update_transfer_state(status, "cd" * 32, "downloading", 60, True)

    assert "Peers: 0 (Seeds: 1) | Connections: 1" in caplog.text
    assert "No connected peers or seeds" not in caplog.text


def test_no_source_warning_reports_connections(tmp_path, caplog) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import TorrentStatusSnapshot
    from mirror.watcher import MirrorWatcher

    watcher = MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
    )
    status = TorrentStatusSnapshot(
        progress=0.0,
        state="downloading",
        num_peers=0,
        num_seeds=0,
        num_connections=2,
        download_rate=0,
        upload_rate=0,
        is_seeding=False,
    )

    with caplog.at_level("WARNING", logger="mirror.watcher"):
        watcher._update_transfer_state(status, "cd" * 32, "downloading", 60, False)

    assert "No connected peers or seeds" in caplog.text
    assert "Connections: 2" in caplog.text


def test_once_mode_stops_monitoring_after_seeding(tmp_path, monkeypatch) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    import mirror.watcher as watcher_module

    class SeedingSession:
        def torrent_metadata(self, info_hash: str):
            return None

        def torrent_status(self, info_hash: str):
            from mirror.libtorrent_session import TorrentStatusSnapshot

            return TorrentStatusSnapshot(
                progress=1.0,
                state="seeding",
                num_peers=0,
                download_rate=0,
                upload_rate=0,
                is_seeding=True,
            )

    watcher = watcher_module.MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
    )
    watcher.session = SeedingSession()
    watcher._active_info_hash = "cd" * 32
    watcher._once_mode = True
    watcher._running = True
    monkeypatch.setattr(watcher_module.time, "sleep", lambda _seconds: None)

    watcher._monitor_active_torrent_loop()

    assert watcher._stop_reason.value == "seeding"
    assert watcher._running is False


def test_seeding_completion_log_only_emits_on_state_transition(tmp_path, caplog) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import TorrentStatusSnapshot
    from mirror.watcher import MirrorWatcher

    watcher = MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
    )
    status = TorrentStatusSnapshot(
        progress=1.0,
        state="seeding",
        num_peers=0,
        download_rate=0,
        upload_rate=0,
        is_seeding=True,
    )

    with caplog.at_level("INFO", logger="mirror.watcher"):
        watcher._update_transfer_state(status, "cd" * 32, "", 0, False)
        watcher._update_transfer_state(status, "cd" * 32, "seeding", 0, False)

    assert caplog.text.count("Snapshot download complete; now seeding") == 1


def test_configured_seed_peer_attempt_is_forwarded(tmp_path) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.watcher import MirrorWatcher

    watcher = MirrorWatcher(
        producer_signing_pubkey_hex="ab" * 32,
        data_dir=str(tmp_path),
        seed_peers=[("seed.example", 6881)],
    )
    session = FakeSession()
    watcher.session = session

    watcher._connect_seed_peers("cd" * 32)

    assert session.calls == [("connect", "cd" * 32, "seed.example", 6881)]


def test_libtorrent_stop_saves_resume_data_before_releasing_session(tmp_path) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import LibtorrentSession

    class FakeNativeSession:
        pass

    session = LibtorrentSession(data_dir=str(tmp_path))
    session._session = FakeNativeSession()
    saved: list[str] = []
    session.save_resume_data = lambda: saved.append("resume")
    session.save_dht_state = lambda: saved.append("dht")

    session.stop()

    assert saved == ["resume", "dht"]
    assert session._session is None


def test_peer_lifecycle_alerts_are_debug_only() -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.libtorrent_session import LibtorrentSession

    source = inspect.getsource(LibtorrentSession._alert_loop)
    assert 'logger.debug("Peer connection established: %s", snap.message)' in source
    assert 'logger.debug("Peer disconnected: %s", snap.message)' in source
    assert '"skipping tracker announce (unreachable)" in snap.message' in source


def test_old_authority_pubkey_environment_is_ignored(monkeypatch, caplog) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    from mirror.watcher import resolve_producer_signing_pubkey

    monkeypatch.setenv("AUTHORITY_PUBKEY", "00" * 32)
    monkeypatch.setenv("PRODUCER_SIGNING_PUBKEY", "ab" * 32)

    with caplog.at_level("WARNING"):
        assert resolve_producer_signing_pubkey() == "ab" * 32

    assert "AUTHORITY_PUBKEY is ignored; use PRODUCER_SIGNING_PUBKEY." in caplog.text


def test_start_logs_the_complete_producer_public_key(tmp_path, monkeypatch, caplog) -> None:
    sys.modules.setdefault("libtorrent", SimpleNamespace())
    import mirror.watcher as watcher_module

    class FakeLibtorrentSession:
        def __init__(self, **kwargs) -> None:
            pass

        def start(self) -> None:
            pass

    public_key = "ab" * 32
    watcher = watcher_module.MirrorWatcher(
        producer_signing_pubkey_hex=public_key,
        data_dir=str(tmp_path),
    )
    monkeypatch.setattr(watcher_module, "LibtorrentSession", FakeLibtorrentSession)
    monkeypatch.setattr(watcher_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(watcher, "_run_once", lambda: None)
    monkeypatch.setattr(watcher, "stop", lambda: None)

    with caplog.at_level("INFO", logger="mirror.watcher"):
        watcher.start(once=True)

    assert (
        f"Verification using PRODUCER_SIGNING_PUBKEY: {public_key}"
        in caplog.text
    )
    assert "Producer signing public key (PRODUCER_SIGNING_PUBKEY):" not in caplog.text
