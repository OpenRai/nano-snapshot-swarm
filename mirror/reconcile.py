from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class DesiredSnapshot:
    seq: int
    info_hash: str


@dataclass(frozen=True)
class ReconcileDecision:
    action: Literal["idle", "activate", "replace", "noop"]
    target: Optional[DesiredSnapshot] = None
    should_recheck: bool = False


def reconcile_snapshot(
    *,
    desired: Optional[DesiredSnapshot],
    active_info_hash: Optional[str],
    resumed_info_hash: Optional[str] = None,
) -> ReconcileDecision:
    if desired is None:
        return ReconcileDecision(action="idle")

    if active_info_hash is None:
        return ReconcileDecision(
            action="activate",
            target=desired,
            should_recheck=desired.info_hash == resumed_info_hash,
        )

    if active_info_hash == desired.info_hash:
        return ReconcileDecision(action="noop", target=desired)

    return ReconcileDecision(action="replace", target=desired)
