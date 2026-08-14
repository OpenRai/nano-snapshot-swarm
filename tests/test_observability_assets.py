from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_alloy_is_skipped_without_a_remote_write_endpoint() -> None:
    unit = (PROJECT_ROOT / "systemd/nano-observability.service").read_text()

    assert "ExecCondition=/usr/bin/test -n ${GRAFANA_CLOUD_PROMETHEUS_REMOTE_WRITE_URL}" in unit


def test_environment_template_documents_optional_alloy_collection() -> None:
    template = (PROJECT_ROOT / ".env.example").read_text()

    assert "GRAFANA_CLOUD_PROMETHEUS_REMOTE_WRITE_URL=" in template
    assert "GRAFANA_CLOUD_PROMETHEUS_INSTANCE_ID=" in template
    assert "GRAFANA_CLOUD_PROMETHEUS_WRITE_TOKEN=" in template
    assert "completely bypass" in template
