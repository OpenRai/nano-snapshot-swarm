from __future__ import annotations

import json
import os
import subprocess
import time
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
    assert "OBSERVABILITY_MIN_FREE_BYTES=5368709120" in template
    assert "OBSERVABILITY_REMOTE_WRITE_STALE_SECONDS=600" in template
    assert "OBSERVABILITY_RESTART_COOLDOWN_SECONDS=3600" in template
    assert "completely bypass" in template


def test_alloy_watchdog_bounds_recovery_and_detects_disk_exhaustion() -> None:
    script = (PROJECT_ROOT / "scripts/check-observability.sh").read_text()
    unit = (PROJECT_ROOT / "systemd/nano-observability-watchdog.service").read_text()
    timer = (PROJECT_ROOT / "systemd/nano-observability-watchdog.timer").read_text()

    assert 'MIN_FREE_BYTES="${OBSERVABILITY_MIN_FREE_BYTES:-5368709120}"' in script
    assert 'STALE_SECONDS="${OBSERVABILITY_REMOTE_WRITE_STALE_SECONDS:-600}"' in script
    assert 'RESTART_COOLDOWN_SECONDS="${OBSERVABILITY_RESTART_COOLDOWN_SECONDS:-3600}"' in script
    assert 'systemctl --user restart nano-observability.service' in script
    assert "Reclaim disk space before restarting Alloy" in script
    assert "ExecStart=%h/nano-snapshot-swarm/scripts/check-observability.sh" in unit
    assert "OnUnitActiveSec=5m" in timer
    assert "Persistent=true" in timer


def test_alloy_watchdog_restarts_only_a_stale_collector(tmp_path: Path) -> None:
    script = PROJECT_ROOT / "scripts/check-observability.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    systemctl = bin_dir / "systemctl"
    restart_log = tmp_path / "restart.log"

    curl.write_text(
        "#!/usr/bin/env bash\n"
        "case \"${!#}\" in\n"
        "  *9108*) printf '%s\\n' 'nano_snapshot_ready 1' ;;\n"
        "  *12345*) printf '%s %s\\n' "
        "'prometheus_remote_storage_queue_highest_sent_timestamp_seconds{"
        "component_id=\"prometheus.remote_write.grafana_cloud\"}' "
        "\"${TEST_LAST_SENT:?}\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"${TEST_RESTART_LOG:?}\"\n"
    )
    curl.chmod(0o755)
    systemctl.chmod(0o755)

    base_env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "OBSERVABILITY_MIN_FREE_BYTES": "1",
        "OBSERVABILITY_REMOTE_WRITE_STALE_SECONDS": "600",
        "OBSERVABILITY_RESTART_COOLDOWN_SECONDS": "3600",
        "TEST_RESTART_LOG": str(restart_log),
    }

    fresh = subprocess.run(
        [script],
        check=False,
        capture_output=True,
        text=True,
        env={**base_env, "TEST_LAST_SENT": str(int(time.time()))},
    )
    assert fresh.returncode == 0
    assert "remote write is current" in fresh.stdout
    assert not restart_log.exists()

    stale = subprocess.run(
        [script],
        check=False,
        capture_output=True,
        text=True,
        env={**base_env, "TEST_LAST_SENT": str(int(time.time()) - 601)},
    )
    assert stale.returncode == 0
    assert "restarting nano-observability.service" in stale.stdout
    assert restart_log.read_text().strip() == "--user restart nano-observability.service"


def test_observability_guide_links_the_live_public_dashboard() -> None:
    guide = (PROJECT_ROOT / "docs/observability.md").read_text()

    assert (
        "https://grandoat1733.grafana.net/public-dashboards/"
        "67d611ed1e1849a2abf21284747d4776"
    ) in guide


def test_dashboard_preserves_the_public_panel_optimizations() -> None:
    dashboard = json.loads(
        (PROJECT_ROOT / "observability/nano-snapshot-swarm-dashboard.json").read_text()
    )
    panels = {panel["id"]: panel for panel in dashboard["panels"]}

    assert all(
        panel["datasource"]["uid"] == "grafanacloud-prom"
        for panel in panels.values()
    )
    assert dashboard["schemaVersion"] == 42
    assert dashboard["version"] == 9
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
    assert (
        panels[7]["transformations"][1]["options"]["renameByName"]["info_hash"]
        == "Info Hash"
    )
