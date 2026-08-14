from __future__ import annotations

from prometheus_client import generate_latest

from shared.metrics import SnapshotMetrics, _boolean_from_env


def test_boolean_environment_defaults_and_rejects_invalid_values(monkeypatch) -> None:
    monkeypatch.delenv("METRICS_ENABLED", raising=False)
    assert _boolean_from_env("METRICS_ENABLED", default=True) is True

    monkeypatch.setenv("METRICS_ENABLED", "")
    assert _boolean_from_env("METRICS_ENABLED", default=True) is True

    monkeypatch.setenv("METRICS_ENABLED", "FALSE")
    assert _boolean_from_env("METRICS_ENABLED", default=True) is False

    monkeypatch.setenv("METRICS_ENABLED", "no")
    try:
        _boolean_from_env("METRICS_ENABLED", default=True)
    except ValueError as exc:
        assert "METRICS_ENABLED must be true or false" in str(exc)
    else:
        raise AssertionError("expected METRICS_ENABLED=no to fail")


def test_metrics_expose_exact_generation_and_bounded_transfer_labels() -> None:
    metrics = SnapshotMetrics("producer")
    info_hash = "ab" * 32

    metrics.observe_generation(
        info_hash=info_hash,
        sequence=1370,
        size_bytes=128,
        original_filename="snapshot-2026-08-14.7z",
    )
    metrics.observe_transfer(
        total_upload=100,
        total_download=200,
        peers=2,
        seeds=3,
        connections=5,
    )
    metrics.observe_transfer(
        total_upload=150,
        total_download=230,
        peers=1,
        seeds=4,
        connections=5,
    )
    metrics.observe_state("seeding", ready=True)

    rendered = generate_latest(metrics.registry).decode()

    assert 'nano_snapshot_generation_info{info_hash="' + info_hash in rendered
    assert 'sequence="1370"' in rendered
    assert 'original_filename="snapshot-2026-08-14.7z"' in rendered
    assert 'nano_snapshot_dht_sequence{service="producer"} 1370.0' in rendered
    assert 'nano_snapshot_size_bytes{service="producer"} 128.0' in rendered
    assert "nano_snapshot_bytes_uploaded_total 150.0" in rendered
    assert "nano_snapshot_bytes_downloaded_total 230.0" in rendered
    assert 'nano_snapshot_swarm_peers{role="leecher"} 1.0' in rendered
    assert 'nano_snapshot_swarm_peers{role="seeder"} 4.0' in rendered
    assert "nano_snapshot_bytes_uploaded_total{info_hash=" not in rendered
    assert "nano_snapshot_swarm_peers{info_hash=" not in rendered


def test_counter_ignores_decreasing_libtorrent_totals_after_generation_change() -> None:
    metrics = SnapshotMetrics("mirror")
    metrics.observe_transfer(
        total_upload=100,
        total_download=200,
        peers=0,
        seeds=1,
        connections=1,
    )
    metrics.observe_transfer(
        total_upload=20,
        total_download=30,
        peers=0,
        seeds=1,
        connections=1,
    )

    rendered = generate_latest(metrics.registry).decode()
    assert "nano_snapshot_bytes_uploaded_total 100.0" in rendered
    assert "nano_snapshot_bytes_downloaded_total 200.0" in rendered
