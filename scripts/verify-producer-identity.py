#!/usr/bin/env python3
"""Check that the checked-in producer identity agrees across deployment files."""

from __future__ import annotations

import ast
import os
import re
import sys
import tomllib
from pathlib import Path

from shared.nano_identity import derive_nano_address

KEY_PATTERN = re.compile(r"[0-9a-f]{64}")


def _read_public_key(path: Path) -> str:
    public_key = path.read_text(encoding="ascii").strip()
    if not KEY_PATTERN.fullmatch(public_key):
        raise ValueError(f"{path} must contain exactly 64 lowercase hex characters")
    return public_key


def _read_fly_public_key(path: Path) -> str:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    try:
        public_key = config["env"]["PRODUCER_SIGNING_PUBKEY"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path} is missing env.PRODUCER_SIGNING_PUBKEY") from exc
    if not isinstance(public_key, str) or not KEY_PATTERN.fullmatch(public_key):
        raise ValueError(f"{path} contains an invalid PRODUCER_SIGNING_PUBKEY")
    return public_key


def _read_status_api_default(path: Path) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        has_expected_name = any(
            isinstance(target, ast.Name)
            and target.id == "DEFAULT_PRODUCER_SIGNING_PUBKEY"
            for target in node.targets
        )
        if not has_expected_name:
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            public_key = node.value.value
            if KEY_PATTERN.fullmatch(public_key):
                return public_key
    raise ValueError(f"{path} is missing a valid DEFAULT_PRODUCER_SIGNING_PUBKEY")


def check_identity(repo_root: Path) -> None:
    public_key = _read_public_key(repo_root / "PRODUCER_SIGNING_PUBKEY")
    fly_public_key = _read_fly_public_key(repo_root / "status-api" / "fly.toml")
    status_api_public_key = _read_status_api_default(
        repo_root / "status-api" / "app" / "main.py"
    )
    if public_key != fly_public_key or public_key != status_api_public_key:
        raise ValueError(
            "producer public key disagrees across repository and Status API configuration"
        )

    private_key = os.environ.get("DHT_PRIVATE_KEY")
    if private_key:
        try:
            derived_public_key, _ = derive_nano_address(private_key)
        except (ImportError, ValueError) as exc:
            raise ValueError("DHT_PRIVATE_KEY is invalid") from exc
        if derived_public_key.hex() != public_key:
            raise ValueError("DHT_PRIVATE_KEY derives a different producer public key")
        print("DHT_PRIVATE_KEY matches the configured producer public key")
    else:
        print("DHT_PRIVATE_KEY is not set; private-key derivation check skipped")

    print("Producer public key matches the repository and Fly configuration")


if __name__ == "__main__":
    try:
        check_identity(Path(__file__).resolve().parent.parent)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
