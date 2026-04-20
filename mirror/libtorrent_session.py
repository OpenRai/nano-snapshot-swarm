from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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

    return AlertSnapshot(type_name=type_name, category=cat, message=msg, extra=extra)


class LibtorrentSession:
    def __init__(
        self,
        listen_port: int = 6881,
        data_dir: str = "/data",
        enable_dht: bool = True,
    ):
        self.data_dir = data_dir
        self._listen_port = listen_port
        self._enable_dht = enable_dht
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
            "enable_incoming_utp": True,
            "enable_outgoing_utp": True,
            "enable_incoming_tcp": True,
            "enable_outgoing_tcp": True,
            "alert_mask": lt.alert.category_t.all_categories,
            "download_rate_limit": 0,
            "upload_rate_limit": 0,
        }

        # Load saved DHT state for faster re-bootstrap
        dht_state_path = Path(self.data_dir) / ".dht_state"
        if dht_state_path.exists():
            try:
                state = lt.bdecode(dht_state_path.read_bytes())
                settings["dht_state"] = state
                logger.info("Loaded saved DHT state from %s", dht_state_path)
            except Exception as e:
                logger.warning("Failed to load DHT state: %s", e)

        self._session = lt.session(settings)
        self._dht_state_path = dht_state_path

        for host, port in DHT_BOOTSTRAP_NODES:
            self._session.add_dht_node((host, port))

        self._running = True
        self._dht_bootstrapped = threading.Event()
        self._alert_thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._alert_thread.start()
        logger.info(f"libtorrent session started, listening on port {self._listen_port}")

    def stop(self) -> None:
        self._running = False
        if self._alert_thread:
            self._alert_thread.join(timeout=10)
        if self._session:
            self.save_dht_state()
            for handle in self._handles.values():
                try:
                    handle.save_resume_data(lt.torrent_handle.save_settings)
                except Exception:
                    pass
            self._session = None
        logger.info("libtorrent session stopped")

    def add_torrent(
        self,
        info_hash: str,
        save_path: Optional[str] = None,
        torrent_file: Optional[str] = None,
        web_seeds: Optional[list[str]] = None,
    ) -> lt.torrent_handle:
        if self._session is None:
            raise RuntimeError("Session not started")

        save_path = save_path or self.data_dir

        if torrent_file:
            info = lt.torrent_info(torrent_file)
            params = {
                "ti": info,
                "save_path": save_path,
            }
            flags = lt.torrent_flags.auto_managed
            if hasattr(lt.torrent_flags, "update_subscribe"):
                flags |= lt.torrent_flags.update_subscribe
            params["flags"] = flags
            handle = self._session.add_torrent(params)
        else:
            magnet_uri = f"magnet:?xt=urn:btmh:{info_hash}"
            if web_seeds:
                for ws in web_seeds:
                    magnet_uri += f"&ws={ws}"
            params = lt.parse_magnet_uri(magnet_uri)
            params.save_path = save_path
            params.flags = lt.torrent_flags.auto_managed
            if hasattr(lt.torrent_flags, "update_subscribe"):
                params.flags |= lt.torrent_flags.update_subscribe
            handle = self._session.add_torrent(params)

        self._handles[info_hash] = handle
        logger.info(f"Added torrent: {info_hash[:16]}...")
        return handle

    def remove_torrent(self, info_hash: str) -> None:
        handle = self._handles.pop(info_hash, None)
        if handle and self._session:
            self._session.remove_torrent(handle)
            logger.info(f"Removed torrent: {info_hash[:16]}...")

    def pause_torrent(self, info_hash: str) -> None:
        handle = self._handles.get(info_hash)
        if handle:
            handle.pause()
            logger.info(f"Paused torrent: {info_hash[:16]}...")

    def resume_torrent(self, info_hash: str) -> None:
        handle = self._handles.get(info_hash)
        if handle:
            handle.resume()
            logger.info(f"Resumed torrent: {info_hash[:16]}...")

    def force_recheck(self, info_hash: str) -> None:
        handle = self._handles.get(info_hash)
        if handle:
            handle.force_recheck()
            logger.info(f"Force recheck started: {info_hash[:16]}...")

    def get_handle(self, info_hash: str) -> Optional[lt.torrent_handle]:
        return self._handles.get(info_hash)

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
        self, type_name: str, timeout: float = 60.0
    ) -> Optional[AlertSnapshot]:
        """Wait for an alert snapshot with the given type name."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._alert_event.wait(timeout=1.0)
            with self._alert_lock:
                self._alert_event.clear()
                for snap in self._alerts:
                    if snap.type_name == type_name:
                        self._alerts.remove(snap)
                        return snap
        return None

    def pop_alerts(self) -> list[AlertSnapshot]:
        """Return and clear all accumulated alert snapshots."""
        with self._alert_lock:
            alerts = self._alerts[:]
            self._alerts.clear()
            return alerts

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
                        if snap.category & lt.alert.category_t.error_notification:
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
        logger.info(f"DHT put_mutable_item requested for salt='{salt}', seq={seq}")
