#!/usr/bin/env python3
"""Validate a captured producer/mirror beta E2E evidence bundle.

The capture format is intentionally plain JSON so an operator can collect it
from SSH, Docker, and the dashboard without granting this repository access to
those systems. See docs/manual-e2e-validation.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.evidence.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read evidence: {exc}")

    publications = evidence.get("publications")
    if not isinstance(publications, list) or len(publications) < 3:
        fail("publications must contain initial plus two mutations")
    sequences = [item.get("dht_sequence") for item in publications]
    hashes = [item.get("info_hash") for item in publications]
    if any(not isinstance(seq, int) for seq in sequences):
        fail("every publication needs an integer dht_sequence")
    if any(not isinstance(value, str) or len(value) != 64 for value in hashes):
        fail("every publication needs a 64-character v2 info_hash")
    if sequences != sorted(set(sequences)):
        fail(f"DHT sequences are not strictly increasing: {sequences}")
    if len(set(hashes)) != len(hashes):
        fail("consecutive publications must have distinct info hashes")

    mirror = evidence.get("mirror", {})
    if mirror.get("final_phase") != "seeding":
        fail("mirror final_phase must be seeding")
    if mirror.get("container_stayed_running") is not True:
        fail("mirror must stay running across an update")
    if mirror.get("discovered_sequences") != sequences:
        fail("mirror discovered sequences do not match producer evidence")
    if mirror.get("discovered_hashes") != hashes:
        fail("mirror discovered hashes do not match producer evidence")

    producer = evidence.get("producer", {})
    if producer.get("restart_monotonic") is not True:
        fail("producer restart_monotonic must be true")
    if producer.get("all_dht_verified") is not True:
        fail("all producer publications must be DHT verified")

    dashboard = evidence.get("dashboard", {})
    if dashboard.get("final_info_hash") != hashes[-1]:
        fail("dashboard final hash does not match the final publication")
    if dashboard.get("verified") is not True:
        fail("dashboard final publication is not verified")

    print(
        "PASS: %d strictly monotonic publications, mirror reseeded every update, "
        "producer restart and dashboard agreement verified"
        % len(publications)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
