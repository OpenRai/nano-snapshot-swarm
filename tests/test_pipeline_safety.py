from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_daily_pipeline_does_not_interpolate_metadata_into_python_source() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "python3 -c" not in script
    assert "python -c" not in script
    assert "private_key_hex='$DHT_PRIVATE_KEY'" not in script
    assert "original_filename': '$FILENAME'" not in script
    assert 'open(\'$META_FILE\'' not in script


def test_daily_pipeline_reloads_active_seeder_or_starts_an_inactive_one() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "systemctl --user reload nano-seed.service" in script
    assert "systemctl --user start nano-seed.service" in script
    assert "systemctl --user restart nano-seed.service" not in script
    assert "systemctl --user kill --signal=HUP nano-seed.service" not in script
    assert '"dht_verified"' in script
    assert "--defer-dht-publish" in script
    assert "last_dht_info_hash" in script
    assert "from producer.publish import publish_to_dht" not in script


def test_daily_pipeline_leaves_an_unchanged_snapshot_to_existing_timers() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    unchanged_start = script.index('log "Snapshot unchanged')
    unchanged_end = script.index('exit 0', unchanged_start)
    unchanged_path = script[unchanged_start:unchanged_end]

    assert "no seeder reload or DHT publication requested" in unchanged_path
    assert "systemctl --user" not in unchanged_path
    assert "push-snapshot-status.sh" not in unchanged_path


def test_daily_pipeline_waits_for_the_full_cold_seeder_publication_budget() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert "seq 1 360" in script


def test_daily_pipeline_uses_authoritative_aria2_progress() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()
    rpc_helpers = Path("scripts/aria2-rpc.sh").read_text()

    progress_start = script.index("# Progress poller:")
    progress_end = script.index("# Collect aria2c exit code", progress_start)
    progress = script[progress_start:progress_end]

    assert 'source "$REPO_DIR/scripts/aria2-rpc.sh"' in script
    assert "aria2.tellStatus" in script
    assert '"completedLength"' in script
    assert '"downloadSpeed"' in script
    assert "--enable-rpc=true" in script
    assert "--rpc-listen-all=false" in script
    assert '"token:${ARIA2_RPC_SECRET}"' in script
    assert 'method: "aria2.shutdown"' in script
    assert "stat -c%s" not in progress
    assert 'if ! kill -0 "$ARIA_PID"' in progress
    assert 'ARIA_TERMINAL_STATUS=complete' in progress
    assert "shutdown_aria2" in progress
    assert "RPC_FAILURE_LIMIT=3" in progress
    assert "query_aria2_status" in rpc_helpers
    assert "shutdown_aria2" in rpc_helpers


def _query_aria2_fixture(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    command = r'''
source scripts/aria2-rpc.sh
call_aria2_rpc() { printf '%s' "$ARIA2_TEST_RESPONSE"; }
ARIA2_STATUS_REQUEST='{}'
if query_aria2_status; then
    printf '%s\n' "$ARIA2_STATUS" "$ARIA2_TOTAL_LENGTH" \
        "$ARIA2_COMPLETED_LENGTH" "$ARIA2_DOWNLOAD_SPEED" \
        "$ARIA2_ERROR_CODE" "$ARIA2_ERROR_MESSAGE"
else
    exit 1
fi
'''
    environment = os.environ.copy()
    environment["ARIA2_TEST_RESPONSE"] = json.dumps(payload)
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_aria2_rpc_status_parses_active_and_complete_results() -> None:
    for status, completed in (("active", "524288"), ("complete", "1048576")):
        result = _query_aria2_fixture(
            {
                "jsonrpc": "2.0",
                "id": "progress",
                "result": {
                    "status": status,
                    "totalLength": "1048576",
                    "completedLength": completed,
                    "downloadSpeed": "262144",
                },
            }
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[:4] == [
            status,
            "1048576",
            completed,
            "262144",
        ]


def test_aria2_rpc_status_preserves_terminal_error_details() -> None:
    result = _query_aria2_fixture(
        {
            "jsonrpc": "2.0",
            "id": "progress",
            "result": {
                "status": "error",
                "totalLength": "1048576",
                "completedLength": "524288",
                "downloadSpeed": "0",
                "errorCode": "3",
                "errorMessage": "resource not found",
            },
        }
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "error",
        "1048576",
        "524288",
        "0",
        "3",
        "resource not found",
    ]


def test_aria2_rpc_status_rejects_malformed_or_rpc_error_results() -> None:
    malformed = _query_aria2_fixture({"result": {"status": "active"}})
    rpc_error = _query_aria2_fixture(
        {"jsonrpc": "2.0", "id": "progress", "error": {"message": "not found"}}
    )

    assert malformed.returncode == 1
    assert rpc_error.returncode == 1


def test_aria2_rpc_shutdown_accepts_ok_response() -> None:
    command = r'''
source scripts/aria2-rpc.sh
call_aria2_rpc() { printf '%s' '{"jsonrpc":"2.0","id":"shutdown","result":"OK"}'; }
ARIA2_SHUTDOWN_REQUEST='{}'
shutdown_aria2
'''

    subprocess.run(["bash", "-c", command], check=True)


def test_daily_pipeline_keeps_content_length_as_final_validation() -> None:
    script = Path("scripts/daily-snapshot.sh").read_text()

    assert 'if [ "$DOWNLOADED_SIZE" -ne "$EXPECTED_SIZE" ]' in script
    assert 'Size verified: $(numfmt --to=iec-i --suffix=B "$DOWNLOADED_SIZE")' in script


def test_producer_unit_builds_explicit_sequence_helper_before_start() -> None:
    unit = Path("systemd/nano-seed.service").read_text()

    assert "DHT_PUT_HELPER=%h/nano-snapshot-swarm/bin/nano-dht-put" in unit
    assert "ExecStartPre=%h/nano-snapshot-swarm/scripts/build-dht-put-helper.sh" in unit
    assert "ExecReload=/bin/kill -HUP $MAINPID" in unit


def test_seeder_accepts_reload_before_dht_bootstrap() -> None:
    seeder = Path("producer/seeder.py").read_text()

    hup_registration = seeder.index("signal.signal(signal.SIGHUP, on_signal)")
    assert hup_registration < seeder.index("session = LibtorrentSession(")
    assert hup_registration < seeder.index("session.wait_for_dht_bootstrap(timeout=120)")


def test_shell_special_filename_stays_data_when_passed_as_an_argument(tmp_path) -> None:
    metadata_path = tmp_path / "metadata.json"
    marker = tmp_path / "SHOULD_NOT_RUN"
    filename = f"quote'\\dollar$\\n{marker.name}\narchive.7z"
    writer = "import json, sys; json.dump({'filename': sys.argv[2]}, open(sys.argv[1], 'w'))"

    subprocess.run(
        [sys.executable, "-c", writer, str(metadata_path), filename],
        check=True,
    )

    assert json.loads(metadata_path.read_text())["filename"] == filename
    assert not marker.exists()
