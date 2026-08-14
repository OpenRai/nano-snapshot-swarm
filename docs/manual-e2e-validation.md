# Manual End-to-End Validation

This procedure validates producer publication, DHT discovery, BitTorrent
transfer, and mirror replacement with a small payload. Use an isolated
validation environment. Do not change the production producer's `.env`, output
directory, or `nano-seed.service` to run this test.

This repository does not yet provision the isolated producer automatically.
Treat this as an operator procedure, not a turnkey test command.

## Producer

On an isolated host or temporary checkout, create a separate output directory
and start a separate `producer.seeder` process. Give that process the same
signing key, `DHT_SALT=validation`, `USE_PLACEHOLDER_SNAPSHOT=true`, and its own
`OUTPUT_DIR`. Keep it separate from the production `nano-seed.service`.

The normal `nano-snapshot.service` is not a validation harness. It signals the
production service name and uses the production output directory. Do not run it
after changing only `DHT_SALT`.

Create and publish a placeholder through the isolated producer path. Record the
published DHT sequence and v2 info hash. Confirm that the temporary seeder logs
an authoritative DHT verification before starting the mirror.

The validation output directory must contain a fresh timestamped placeholder, the
canonical symlink, its torrent, and updated `snapshot-meta.json`. The producer
seeder must be active, report the canonical torrent as seeding, and write
`dht_verified=true` with the matching `torrent_info_hash` in `seeder-stats.json`.

## Mirror

Run a temporary mirror container with a separate data directory. Set
`DHT_SALT=validation`, use swarm mode, and shorten `POLL_INTERVAL` for the test.
Do not share the directory with another mirror or producer process.

Acceptance for the first publication:

1. The mirror discovers the signed validation record.
2. The torrent reaches 100% and the mirror enters `seeding`.
3. The mirror remains running after completion.
4. `mirror_state.json` records the discovered sequence and info hash.

Create and publish a second placeholder through the isolated producer. Do not
restart the mirror. Acceptance for mutation:

1. The mirror discovers a higher sequence and different info hash.
2. It pauses/removes the old torrent and resolves the new torrent metadata.
3. It force-rechecks the existing canonical file.
4. It resumes requests for changed pieces and reaches `seeding` again.
5. The mirror container uptime is continuous across the replacement.
6. `mirror_state.json` and `snapshot-meta.json` contain the second hash.

Also restart the temporary producer seeder between two publications. Confirm
that it uses a current DHT sequence for each distinct value. Confirm that the
mirror detects the next update.

Record producer DHT publication logs, mirror transition logs, both info hashes,
file sizes, `Peers` and `Seeds` counters, producer upload counters, and the
final seeding state. Claim peer-sourced transfer only when a mirror shows
nonzero progress with a connected source and the producer reports matching
upload activity.

Stop the temporary producer process and remove its temporary output directory
after collecting evidence. Do not change the production DHT salt to clean up a
validation run.

The hub's **Status updated** time is the time its signed status payload was
pushed. It is not the timestamp embedded in the archive or a claim that the
snapshot content changed; use the DHT sequence and info hash to identify a new
snapshot.

## Evidence gate

Save the captured values in a JSON bundle with this shape:

```json
{
  "publications": [
    {"dht_sequence": 1, "info_hash": "<64 hex>"},
    {"dht_sequence": 2, "info_hash": "<64 hex>"},
    {"dht_sequence": 3, "info_hash": "<64 hex>"}
  ],
  "producer": {
    "restart_monotonic": true,
    "all_dht_verified": true
  },
  "mirror": {
    "final_phase": "seeding",
    "container_stayed_running": true,
    "discovered_sequences": [1, 2, 3],
    "discovered_hashes": ["<64 hex>", "<64 hex>", "<64 hex>"]
  },
  "dashboard": {"final_info_hash": "<64 hex>", "verified": true}
}
```

Run the release gate before closing the E2E issue:

```bash
./scripts/validate-e2e-evidence.py evidence.json
```
