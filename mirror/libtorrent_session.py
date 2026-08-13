from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import libtorrent as lt

logger = logging.getLogger("mirror.session")

DHT_BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]


@dataclass
class AlertSnapshot:
    """Safe copy of alert data — survives after libtorrent frees the alert."""

    type_name: str
    category: int
    message: str
    # Alert-specific fields (extracted before the raw alert is freed)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TorrentStatusSnapshot:
    progress: float
    state: str
    num_peers: int
    download_rate: int
    upload_rate: int
    is_seeding: bool
    total_upload: int = 0
    error: str = ""
    num_seeds: int = 0
    num_connections: int = 0


@dataclass(frozen=True)
class TorrentMetadataSnapshot:
    name: str
    snapshot_meta: Optional[dict[str, Any]] = None


def _snapshot_alert(alert: lt.alert) -> AlertSnapshot:
    """Extract all useful data from a libtorrent alert before it is freed.

    libtorrent alert pointers are only valid until the next pop_alerts() call.
    This function copies everything we need into a plain Python object.
    """
    type_name = type(alert).__name__
    try:
        cat = alert.category()
    except Exception:
        cat = 0
    try:
        msg = str(alert)
    except Exception:
        msg = type_name

    extra: dict[str, Any] = {}

    if isinstance(alert, lt.dht_put_alert):
        extra["num_success"] = getattr(alert, "num_success", None)
        extra["salt"] = getattr(alert, "salt", "")
        extra["seq"] = getattr(alert, "seq", 0)
        try:
            extra["public_key"] = bytes(alert.public_key).hex()
        except Exception:
            pass
        try:
            extra["target"] = str(alert.target)
        except Exception:
            pass
        try:
            extra["signature"] = bytes(alert.signature).hex()
        except Exception:
            pass

    elif isinstance(alert, lt.dht_mutable_item_alert):
        extra["authoritative"] = getattr(alert, "authoritative", False)
        extra["seq"] = getattr(alert, "seq", 0)
        extra["salt"] = getattr(alert, "salt", "")
        try:
            extra["item"] = alert.item
        except Exception:
            extra["item"] = None
        try:
            extra["key"] = bytes(alert.key).hex()
        except Exception:
            pass
        try:
            extra["signature"] = bytes(alert.signature).hex()
        except Exception:
            pass

    elif isinstance(alert, lt.save_resume_data_alert):
        try:
            extra["resume_data"] = alert.resume_data
        except Exception:
            pass
        try:
            extra["info_hash"] = str(alert.handle.info_hashes().v2)
        except Exception:
            pass
    elif isinstance(alert, lt.save_resume_data_failed_alert):
        try:
            extra["info_hash"] = str(alert.handle.info_hashes().v2)
        except Exception:
            pass
        try:
            extra["error"] = str(alert.error)
        except Exception:
            pass
    elif isinstance(alert, lt.fastresume_rejected_alert):
        try:
            extra["info_hash"] = str(alert.handle.info_hashes().v2)
        except Exception:
            pass
        try:
            extra["error"] = str(alert.error)
        except Exception:
            pass

    return AlertSnapshot(type_name=type_name, category=cat, message=msg, extra=extra)


class LibtorrentSession:
    def __init__(
        self,
        listen_port: int = 6881,
        data_dir: str = "/data",
        enable_dht: bool = True,
        load_dht_state: bool = True,
    ):
        self.data_dir = data_dir
        self._listen_port = listen_port
        self._enable_dht = enable_dht
        self._load_dht_state = load_dht_state
        self._session: Optional[lt.session] = None
        self._alert_thread: Optional[threading.Thread] = None
        self._running = False
        self._alerts: list[AlertSnapshot] = []
        self._alert_lock = threading.Lock()
        self._alert_event = threading.Event()
        self._handles: dict[str, lt.torrent_handle] = {}

    def start(self) -> None:
        if self._session is not None:
            logger.warning("Session already started")
            return

        settings = {
            "listen_interfaces": f"0.0.0.0:{self._listen_port},[::]:{self._listen_port}",
            "enable_dht": self._enable_dht,
            "enable_lsd": True,
            # Do not wait for tracker stopped responses during process teardown.
            # The session destructor otherwise waits on unresponsive trackers.
            "stop_tracker_timeout": 0,
            "enable_incoming_utp": True,
            "enable_outgoing_utp": True,
            "enable_incoming_tcp": True,
            "enable_outgoing_tcp": True,
            "alert_mask": lt.alert.category_t.all_categories,
            "download_rate_limit": 0,
            "upload_rate_limit": 0,
        }

        self._session = lt.session(settings)

        # Load saved DHT state for faster re-bootstrap
        self._dht_state_path = Path(self.data_dir) / ".dht_state"
        if self._load_dht_state and self._dht_state_path.exists():
            try:
                state = lt.bdecode(self._dht_state_path.read_bytes())
                self._session.load_state(state, lt.save_state_flags_t.save_dht_state)
                logger.info("Loaded saved DHT state from %s", self._dht_state_path)
            except Exception as e:
                logger.warning("Failed to load DHT state: %s", e)
        elif not self._load_dht_state:
            logger.info("Starting DHT with fresh mutable-item state")

        for host, port in DHT_BOOTSTRAP_NODES:
            self._session.add_dht_node((host, port))

        self._running = True
        self._dht_bootstrapped = threading.Event()
        self._alert_thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._alert_thread.start()
        logger.info(f"libtorrent session started, listening on port {self._listen_port}")

    def stop(self) -> None:
        if self._session:
            self.save_resume_data()
        self._running = False
        if self._alert_thread:
            logger.info("Stopping libtorrent alert loop...")
            self._alert_thread.join(timeout=10)
            if self._alert_thread.is_alive():
                logger.warning("Libtorrent alert loop did not stop within 10s")
        if self._session:
            logger.info("Saving DHT state before releasing libtorrent session...")
            self.save_dht_state()
            logger.info("Releasing libtorrent session...")
            self._session = None
        logger.info("libtorrent session stopped")

    def add_torrent(
        self,
        info_hash: str,
        save_path: Optional[str] = None,
        torrent_file: Optional[str] = None,
        paused: bool = False,
    ) -> lt.torrent_handle:
        """Add a torrent or v2 magnet; see ../docs/torrent-format.md."""
        if self._session is None:
            raise RuntimeError("Session not started")

        save_path = save_path or self.data_dir
        handle_key = info_hash

        info = None
        if torrent_file:
            info = lt.torrent_info(torrent_file)
            handle_key = str(info.info_hashes().v2)

        resume_path = self._resume_path(handle_key)
        resume_params = None
        if resume_path.exists():
            try:
                resume_params = lt.read_resume_data(resume_path.read_bytes())
                logger.info("Loaded saved resume data: v2 info hash=%s...", handle_key[:16])
            except Exception as exc:
                logger.warning(
                    "Ignoring invalid resume data for v2 info hash=%s...: %s",
                    handle_key[:16],
                    exc,
                )
                resume_path.unlink(missing_ok=True)

        if torrent_file:
            assert info is not None
            if resume_params is not None:
                resume_params.ti = info
                resume_params.save_path = save_path
                params = resume_params
            else:
                params = {"ti": info, "save_path": save_path}
            flags = lt.torrent_flags.auto_managed
            if paused:
                flags |= lt.torrent_flags.paused
            if hasattr(lt.torrent_flags, "update_subscribe"):
                flags |= lt.torrent_flags.update_subscribe
            if isinstance(params, dict):
                params["flags"] = flags
            else:
                params.flags = flags
            handle = self._session.add_torrent(params)
        else:
            # Determine magnet URI format based on info hash length
            if len(info_hash) == 40:
                # v1: 20 bytes (40 hex chars) — use btih
                magnet_uri = f"magnet:?xt=urn:btih:{info_hash}"
            else:
                # v2: 32 bytes (64 hex chars) — use btmh with SHA-256 multihash prefix
                magnet_uri = f"magnet:?xt=urn:btmh:1220{info_hash}"
            params = resume_params or lt.parse_magnet_uri(magnet_uri)
            params.save_path = save_path
            params.flags = lt.torrent_flags.auto_managed
            if paused:
                params.flags |= lt.torrent_flags.paused
            if hasattr(lt.torrent_flags, "update_subscribe"):
                params.flags |= lt.torrent_flags.update_subscribe
            handle = self._session.add_torrent(params)

        self._handles[handle_key] = handle
        logger.info("Added torrent: v2 info hash=%s...", handle_key[:16])
        return handle

    def _resume_path(self, info_hash: str) -> Path:
        return Path(self.data_dir) / ".resume" / f"{info_hash}.fastresume"

    def save_resume_data(self, timeout: float = 15.0) -> None:
        """Atomically persist fast-resume data for every active torrent."""
        if not self._session or not self._handles:
            return
        resume_dir = Path(self.data_dir) / ".resume"
        resume_dir.mkdir(parents=True, exist_ok=True)
        for info_hash, handle in list(self._handles.items()):
            self.clear_alerts("save_resume_data_alert")
            self.clear_alerts("save_resume_data_failed_alert")
            try:
                handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
            except Exception as exc:
                logger.warning(
                    "Could not request resume data for v2 info hash=%s...: %s",
                    info_hash[:16],
                    exc,
                )
                continue
            alert = self.wait_for_alert(
                "save_resume_data_alert",
                timeout=timeout,
                predicate=lambda snap, expected=info_hash: snap.extra.get("info_hash")
                == expected,
            )
            if alert is None:
                logger.warning(
                    "Resume data was not saved for v2 info hash=%s...", info_hash[:16]
                )
                continue
            resume_data = alert.extra.get("resume_data")
            if resume_data is None:
                logger.warning("Resume alert had no data for v2 info hash=%s...", info_hash[:16])
                continue
            temporary = resume_dir / f".{info_hash}.fastresume.tmp"
            try:
                temporary.write_bytes(lt.bencode(resume_data))
                temporary.replace(self._resume_path(info_hash))
                logger.info("Saved resume data: v2 info hash=%s...", info_hash[:16])
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                logger.warning(
                    "Could not write resume data for v2 info hash=%s...: %s",
                    info_hash[:16],
                    exc,
                )

    def remove_torrent(self, info_hash: str) -> None:
        handle = self._handles.pop(info_hash, None)
        if handle and self._session:
            self._session.remove_torrent(handle)
            logger.info("Removed torrent: v2 info hash=%s...", info_hash[:16])

    def has_torrent(self, info_hash: str) -> bool:
        return info_hash in self._handles

    def ensure_torrent(
        self,
        info_hash: str,
        save_path: Optional[str] = None,
        paused: bool = False,
    ) -> None:
        if self.has_torrent(info_hash):
            return
        self.add_torrent(
            info_hash=info_hash,
            save_path=save_path,
            paused=paused,
        )

    def pause_torrent(self, info_hash: str) -> None:
        handle = self._handles.get(info_hash)
        if handle:
            handle.pause()
            logger.info("Paused torrent: v2 info hash=%s...", info_hash[:16])

    def resume_torrent(self, info_hash: str) -> None:
        handle = self._handles.get(info_hash)
        if handle:
            handle.resume()
            logger.info("Resumed torrent: v2 info hash=%s...", info_hash[:16])

    def force_recheck(self, info_hash: str) -> None:
        handle = self._handles.get(info_hash)
        if handle:
            handle.force_recheck()
            logger.info("Force recheck started: torrent v2 info hash=%s...", info_hash[:16])

    def get_handle(self, info_hash: str) -> Optional[lt.torrent_handle]:
        return self._handles.get(info_hash)

    def connect_peer(self, info_hash: str, host: str, port: int) -> None:
        handle = self._handles.get(info_hash)
        if handle is None:
            raise KeyError(f"Unknown torrent: {info_hash}")
        ip = socket.gethostbyname(host)
        logger.info("Attempting seed-peer connection: %s:%d", host, port)
        handle.connect_peer((ip, port))

    def torrent_status(self, info_hash: str) -> Optional[TorrentStatusSnapshot]:
        handle = self._handles.get(info_hash)
        if handle is None:
            return None
        status = handle.status()
        return TorrentStatusSnapshot(
            progress=status.progress,
            state=str(status.state),
            num_peers=status.num_peers,
            download_rate=status.download_rate,
            upload_rate=status.upload_rate,
            is_seeding=status.is_seeding,
            total_upload=status.total_upload,
            error=str(getattr(status, "errc", "") or ""),
            num_seeds=getattr(status, "num_seeds", 0),
            num_connections=getattr(status, "num_connections", 0),
        )

    def torrent_metadata(self, info_hash: str) -> Optional[TorrentMetadataSnapshot]:
        handle = self._handles.get(info_hash)
        if handle is None:
            return None
        t_info = handle.torrent_file()
        if not t_info:
            return None

        snapshot_meta: Optional[dict[str, Any]] = None
        try:
            import bencodepy

            raw = (
                t_info.info_section()
                if hasattr(t_info, "info_section")
                else t_info.metadata()
            )
            if raw:
                info_dict = bencodepy.decode(raw)
                x_snapshot = info_dict.get(b"x-snapshot")
                if x_snapshot:
                    snapshot_meta = json.loads(x_snapshot)
        except Exception:
            logger.debug("Could not parse torrent metadata", exc_info=True)

        return TorrentMetadataSnapshot(name=t_info.name(), snapshot_meta=snapshot_meta)

    def dht_node_count(self) -> int:
        """Return the number of DHT nodes in the routing table."""
        if not self._session:
            return 0
        try:
            return self._session.status().dht_nodes  # type: ignore[attr-defined]
        except Exception:
            return 0

    def wait_for_dht_bootstrap(self, timeout: float = 120.0) -> bool:
        """Wait for dht_bootstrap_alert, returns True if bootstrap completed."""
        logger.info("Waiting for DHT bootstrap (up to %.0fs)...", timeout)
        if self._dht_bootstrapped.wait(timeout=timeout):
            return True
        nodes = self.dht_node_count()
        logger.warning("DHT bootstrap alert not received after %.0fs (%d nodes)", timeout, nodes)
        return False

    def save_dht_state(self) -> None:
        """Save DHT state to disk for faster re-bootstrap on restart."""
        if not self._session:
            return
        try:
            entry = self._session.save_state(lt.save_state_flags_t.save_dht_state)
            data = lt.bencode(entry)
            self._dht_state_path.write_bytes(data)
            logger.debug("Saved DHT state to %s", self._dht_state_path)
        except Exception as e:
            logger.warning("Failed to save DHT state: %s", e)

    def wait_for_alert(
        self,
        type_name: str,
        timeout: float = 60.0,
        predicate: Optional[Callable[[AlertSnapshot], bool]] = None,
    ) -> Optional[AlertSnapshot]:
        """Wait for an alert snapshot with the given type name."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._alert_event.wait(timeout=1.0)
            with self._alert_lock:
                self._alert_event.clear()
                for snap in self._alerts:
                    if snap.type_name == type_name and (
                        predicate is None or predicate(snap)
                    ):
                        self._alerts.remove(snap)
                        return snap
        return None

    def wait_for_dht_mutable_item(
        self,
        *,
        salt: str,
        timeout: float = 60.0,
        authoritative_only: bool = False,
    ) -> Optional[AlertSnapshot]:
        def matches(snap: AlertSnapshot) -> bool:
            if snap.extra.get("salt", "") != salt:
                return False
            return not authoritative_only or snap.extra.get("authoritative") is True

        return self.wait_for_alert(
            "dht_mutable_item_alert",
            timeout=timeout,
            predicate=matches,
        )

    def wait_for_dht_put(
        self,
        timeout: float = 60.0,
        *,
        salt: Optional[str] = None,
    ) -> Optional[AlertSnapshot]:
        predicate = None
        if salt is not None:
            def matches_salt(snap: AlertSnapshot) -> bool:
                return snap.extra.get("salt", "") == salt

            predicate = matches_salt
        return self.wait_for_alert("dht_put_alert", timeout=timeout, predicate=predicate)

    def clear_alerts(self, type_name: Optional[str] = None) -> None:
        """Discard queued alert snapshots before starting a new operation."""
        with self._alert_lock:
            if type_name is None:
                self._alerts.clear()
            else:
                self._alerts = [snap for snap in self._alerts if snap.type_name != type_name]

    def publish_dht_mutable_item(
        self,
        private_key_64: bytes,
        public_key: bytes,
        value: bytes,
        salt: str = "daily",
    ) -> None:
        """Start a libtorrent mutable-item put on the live session."""
        if self._session is None:
            raise RuntimeError("Session not started")
        self.clear_alerts("dht_put_alert")
        self._session.dht_put_mutable_item(
            private_key_64,
            public_key,
            value,
            salt.encode("utf-8"),
        )
        logger.info("DHT mutable-item publish requested: salt=%r", salt)

    def _alert_loop(self) -> None:
        while self._running and self._session:
            try:
                new_alerts = self._session.pop_alerts()
                if new_alerts:
                    # Snapshot all alerts IMMEDIATELY — raw alert pointers
                    # become invalid on the next pop_alerts() call.
                    snapshots = [_snapshot_alert(a) for a in new_alerts]
                    with self._alert_lock:
                        self._alerts.extend(snapshots)
                    self._alert_event.set()
                    for snap in snapshots:
                        if snap.type_name == "dht_bootstrap_alert":
                            logger.info("DHT bootstrap complete (%d nodes)", self.dht_node_count())
                            self._dht_bootstrapped.set()
                        elif snap.type_name == "peer_connect_alert":
                            logger.debug("Peer connection established: %s", snap.message)
                        elif snap.type_name == "peer_disconnected_alert":
                            logger.debug("Peer disconnected: %s", snap.message)
                        elif snap.type_name == "fastresume_rejected_alert":
                            logger.warning(
                                "Resume data rejected for v2 info hash=%s...: %s",
                                snap.extra.get("info_hash", "unknown")[:16],
                                snap.extra.get("error", snap.message),
                            )
                        if snap.category & lt.alert.category_t.error_notification:
                            if "dropped alerts" in snap.message:
                                logger.debug(f"libtorrent: {snap.message}")
                            elif "UPnP" in snap.message or "NAT-PMP" in snap.message:
                                logger.debug(f"libtorrent: {snap.message}")
                            else:
                                logger.warning(f"libtorrent alert: {snap.message}")
            except Exception as e:
                if self._running:
                    logger.error(f"Alert loop error: {e}")
            time.sleep(0.5)

    @property
    def is_dht_running(self) -> bool:
        if self._session is None:
            return False
        return self._session.is_dht_running()

    def dht_get_mutable_item(self, public_key: bytes, salt: str = "daily") -> None:
        if self._session is None:
            raise RuntimeError("Session not started")
        pk = public_key if isinstance(public_key, bytes) else public_key.encode("latin-1")
        salt_bytes = salt.encode("utf-8") if isinstance(salt, str) else salt
        self._session.dht_get_mutable_item(pk, salt_bytes)
        logger.info(f"DHT get_mutable_item requested for salt='{salt}'")

    def dht_put_mutable_item(
        self,
        public_key: bytes,
        value: bytes,
        signature: bytes,
        seq: int,
        salt: str = "daily",
    ) -> None:
        if self._session is None:
            raise RuntimeError("Session not started")

        def callback(_entry, sign, _new_seq, _new_salt):
            sign[:] = signature

        pk_list = [int(b) for b in public_key]
        self._session.dht_put_item(pk_list, callback, salt.encode("utf-8"))
        logger.info(
            "DHT mutable-item put requested: salt=%r, sequence=%s", salt, seq
        )
