from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import libtorrent as lt

logger = logging.getLogger("mirror.session")

DHT_BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]


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
        self._alerts: list[lt.alert] = []
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

        self._session = lt.session(settings)

        for host, port in DHT_BOOTSTRAP_NODES:
            self._session.add_dht_node((host, port))

        self._running = True
        self._alert_thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._alert_thread.start()
        logger.info(f"libtorrent session started, listening on port {self._listen_port}")

    def stop(self) -> None:
        self._running = False
        if self._alert_thread:
            self._alert_thread.join(timeout=10)
        if self._session:
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

    def wait_for_alert(self, alert_type: type, timeout: float = 60.0) -> Optional[lt.alert]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._alert_event.wait(timeout=1.0)
            with self._alert_lock:
                self._alert_event.clear()
                for alert in self._alerts:
                    if isinstance(alert, alert_type):
                        self._alerts.remove(alert)
                        return alert
        return None

    def pop_alerts(self) -> list[lt.alert]:
        with self._alert_lock:
            alerts = self._alerts[:]
            self._alerts.clear()
            return alerts

    def _alert_loop(self) -> None:
        while self._running and self._session:
            try:
                new_alerts = self._session.pop_alerts()
                if new_alerts:
                    with self._alert_lock:
                        self._alerts.extend(new_alerts)
                    self._alert_event.set()
                    for alert in new_alerts:
                        if (
                            hasattr(alert, "category")
                            and alert.category() & lt.alert.category_t.error_notification
                        ):
                            logger.warning(f"libtorrent alert: {alert}")
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
        # Pass bytes directly — str would be UTF-8 encoded by Python→C++, corrupting binary keys
        pk = public_key if isinstance(public_key, bytes) else public_key.encode('latin-1')
        salt_bytes = salt.encode('utf-8') if isinstance(salt, str) else salt
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
