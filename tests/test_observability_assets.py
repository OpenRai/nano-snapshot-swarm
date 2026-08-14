from __future__ import annotations

import json
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


def test_dashboard_preserves_the_public_panel_optimizations() -> None:
    dashboard = json.loads(
        (PROJECT_ROOT / "observability/nano-snapshot-swarm-dashboard.json").read_text()
    )
    panels = {panel["id"]: panel for panel in dashboard["panels"]}

    assert all(
        panel["datasource"]["uid"] == "grafanacloud-grandoat1733-prom"
        for panel in panels.values()
    )
    assert dashboard["schemaVersion"] == 42
    assert dashboard["version"] == 8
    assert panels[1]["fieldConfig"]["defaults"]["mappings"][0]["options"]["1"]["text"] == "Ready"
    assert panels[5]["options"]["legend"]["displayMode"] == "table"
    assert panels[5]["options"]["legend"]["calcs"] == ["mean", "max", "lastNotNull"]
    assert [target["expr"] for target in panels[5]["targets"]] == [
        "rate(nano_snapshot_bytes_uploaded_total[5m])",
        "rate(nano_snapshot_bytes_downloaded_total[5m])",
    ]
    assert panels[6]["fieldConfig"]["overrides"][0]["matcher"]["options"] == "connections"
    assert panels[7]["transformations"][1]["id"] == "organize"
    assert (
        panels[7]["transformations"][1]["options"]["renameByName"]["original_filename"]
        == "Original filename"
    )
