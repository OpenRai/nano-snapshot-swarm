from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("mirror.watcher")


class SnapshotMetadata:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, object] = {}
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            return
        try:
            loaded = json.loads(p.read_text())
            if isinstance(loaded, dict):
                self.data = loaded
        except json.JSONDecodeError as e:
            logger.warning("Corrupted snapshot metadata file, resetting: %s", e)
            self.data = {}

    def _save(self) -> None:
        Path(self.path).write_text(json.dumps(self.data, indent=2) + "\n")

    def set(self, data: dict[str, object]) -> None:
        self.data = dict(data)
        self._save()

    def update(self, **fields: object) -> None:
        for key, value in fields.items():
            if value is not None:
                self.data[key] = value
        self._save()


class MirrorState:
    def __init__(self, path: str):
        self.path = path
        self.last_seq: int = 0
        self.last_info_hash: str = ""
        self.current_torrent_name: str = ""
        self.phase: str = "starting"
        self.last_error: str = ""
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.last_seq = data.get("last_seq", 0)
                self.last_info_hash = data.get("last_info_hash", "")
                self.current_torrent_name = data.get("current_torrent_name", "")
                self.phase = data.get("phase", "starting")
                self.last_error = data.get("last_error", "")
                logger.info(
                    "Loaded state: seq=%s, hash=%s..., phase=%s",
                    self.last_seq,
                    self.last_info_hash[:16],
                    self.phase,
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Corrupted state file, resetting: {e}")
                self._save()
        else:
            self._save()

    def _save(self) -> None:
        data = {
            "last_seq": self.last_seq,
            "last_info_hash": self.last_info_hash,
            "current_torrent_name": self.current_torrent_name,
            "phase": self.phase,
            "last_error": self.last_error,
        }
        Path(self.path).write_text(json.dumps(data, indent=2))

    def update(self, seq: int, info_hash: str, torrent_name: str | None = None) -> None:
        self.last_seq = seq
        self.last_info_hash = info_hash
        if torrent_name is not None:
            self.current_torrent_name = torrent_name
        self._save()
        logger.info(f"State updated: seq={seq}, hash={info_hash[:16]}...")

    def set_phase(self, phase: str, last_error: str = "") -> None:
        self.phase = phase
        self.last_error = last_error
        self._save()
