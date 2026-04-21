from __future__ import annotations

from mirror.reconcile import DesiredSnapshot, reconcile_snapshot


def test_reconcile_activates_when_desired_exists_without_active() -> None:
    desired = DesiredSnapshot(seq=5, info_hash="ab" * 32)

    decision = reconcile_snapshot(desired=desired, active_info_hash=None)

    assert decision.action == "activate"
    assert decision.target == desired
    assert decision.should_recheck is False


def test_reconcile_marks_resume_as_recheck() -> None:
    desired = DesiredSnapshot(seq=5, info_hash="ab" * 32)

    decision = reconcile_snapshot(
        desired=desired,
        active_info_hash=None,
        resumed_info_hash="ab" * 32,
    )

    assert decision.action == "activate"
    assert decision.should_recheck is True


def test_reconcile_is_noop_for_same_active_hash() -> None:
    desired = DesiredSnapshot(seq=6, info_hash="cd" * 32)

    decision = reconcile_snapshot(desired=desired, active_info_hash="cd" * 32)

    assert decision.action == "noop"
    assert decision.target == desired


def test_reconcile_replaces_when_hash_changes() -> None:
    desired = DesiredSnapshot(seq=7, info_hash="ef" * 32)

    decision = reconcile_snapshot(desired=desired, active_info_hash="cd" * 32)

    assert decision.action == "replace"
    assert decision.target == desired


def test_reconcile_idles_without_desired_snapshot() -> None:
    decision = reconcile_snapshot(desired=None, active_info_hash=None)

    assert decision.action == "idle"
    assert decision.target is None
