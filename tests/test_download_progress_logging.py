from __future__ import annotations

from mirror.download_progress import DownloadProgressLogState


def begin_download(state: DownloadProgressLogState, now: float = 100.0) -> None:
    assert not state.observe("downloading_metadata", 0.0, now)
    assert not state.observe("downloading", 0.0, now)


def test_initial_download_log_waits_thirty_seconds() -> None:
    state = DownloadProgressLogState()
    begin_download(state)

    assert not state.observe("downloading", 0.01, 129.9)
    assert state.observe("downloading", 0.01, 130.0)


def test_recheck_to_download_starts_progress_logging() -> None:
    state = DownloadProgressLogState()
    assert not state.observe("checking_files", 0.5, 100.0)
    assert not state.observe("downloading", 0.5, 100.0)
    assert state.observe("downloading", 0.5, 130.0)


def test_progress_increase_triggers_and_resets_baseline_and_timer() -> None:
    state = DownloadProgressLogState()
    begin_download(state)
    assert state.observe("downloading", 0.0, 130.0)

    assert not state.observe("downloading", 0.019, 131.0)
    assert state.observe("downloading", 0.02, 132.0)
    assert not state.observe("downloading", 0.039, 133.0)
    assert state.observe("downloading", 0.04, 134.0)


def test_five_minute_timer_triggers_without_progress() -> None:
    state = DownloadProgressLogState()
    begin_download(state)
    assert state.observe("downloading", 0.0, 130.0)

    assert not state.observe("downloading", 0.0, 429.9)
    assert state.observe("downloading", 0.0, 430.0)


def test_completion_does_not_require_progress_scheduler() -> None:
    state = DownloadProgressLogState()
    begin_download(state)

    assert not state.observe("seeding", 1.0, 101.0)
