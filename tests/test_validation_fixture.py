from __future__ import annotations

import pytest

from producer.validation_fixture import parse_size_bytes


def test_parse_size_bytes_accepts_suffixes() -> None:
    assert parse_size_bytes("1k") == 1024
    assert parse_size_bytes("1m") == 1024**2
    assert parse_size_bytes("1g") == 1024**3
    assert parse_size_bytes("1.5m") == int(1.5 * 1024**2)


def test_parse_size_bytes_accepts_raw_integer() -> None:
    assert parse_size_bytes("4096") == 4096


def test_parse_size_bytes_rejects_empty_string() -> None:
    with pytest.raises(ValueError):
        parse_size_bytes("   ")


def test_validation_publish_dry_run_creates_torrent_for_noncanonical_archive(
    monkeypatch, tmp_path, capsys
) -> None:
    import argparse
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "libtorrent", SimpleNamespace())
    import producer.cli as cli
    from producer.torrent_create import TorrentHashes

    archive = tmp_path / "validation;$(touch SHOULD_NOT_RUN).7z"
    archive.write_bytes(b"fixture")
    created = {}

    def fake_create_torrent(**kwargs):
        created.update(kwargs)
        return str(tmp_path / "fixture.torrent"), TorrentHashes(v1="11" * 20, v2="22" * 32)

    monkeypatch.setattr(cli, "create_torrent", fake_create_torrent)
    monkeypatch.setattr(
        cli,
        "publish_to_dht",
        lambda **kwargs: {"dry_run": kwargs["dry_run"], "info_hash_hex": kwargs["info_hash_hex"]},
    )

    cli.cmd_validation_fixture_publish(
        argparse.Namespace(
            private_key="ab" * 32,
            output_dir=str(tmp_path),
            archive_name=archive.name,
            piece_size=32 * 1024 * 1024,
            state_file=str(tmp_path / "state.json"),
            dry_run=True,
            salt="validation",
        )
    )

    assert created["filepath"] == str(archive)
    assert created["output_path"] == f"{archive}.torrent"
    assert not (tmp_path / "SHOULD_NOT_RUN").exists()
    output = capsys.readouterr().out
    assert '"dry_run": true' in output
    assert "Info-hash (v2): " + "22" * 32 in output
