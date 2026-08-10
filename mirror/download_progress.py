from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DownloadProgressLogState:
    """Schedule bounded progress logs for one torrent download phase."""

    previous_state: str = ""
    phase_started_at: float | None = None
    last_logged_at: float | None = None
    last_logged_progress: float = 0.0

    def observe(self, state: str, progress: float, now: float) -> bool:
        if state != self.previous_state:
            if self.previous_state == "downloading_metadata" and state == "downloading":
                self.phase_started_at = now
                self.last_logged_at = None
                self.last_logged_progress = progress
            self.previous_state = state

        if state != "downloading" or self.phase_started_at is None:
            return False

        if self.last_logged_at is None:
            should_log = now - self.phase_started_at >= 30
        else:
            should_log = (
                progress - self.last_logged_progress >= 0.02
                or now - self.last_logged_at >= 300
            )

        if should_log:
            self.last_logged_at = now
            self.last_logged_progress = progress
        return should_log
