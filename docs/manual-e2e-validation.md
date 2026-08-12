# Manual End-to-End Validation

This procedure validates the producer, DHT update, BitTorrent transfer, and
mirror replacement path with small local payloads. It uses the isolated
`validation` DHT salt and does not modify the production stream.

## Producer

On the producer host, ensure the normal user-level `nano-seed.service` is
available and set the temporary test flag in `~/.env`:

```text
USE_PLACEHOLDER_SNAPSHOT=1
DHT_SALT=validation
```

Trigger the pipeline and record the published sequence and v2 info hash:

```bash
systemctl --user start nano-snapshot.service
journalctl --user -u nano-snapshot -f
```

The output directory must contain a fresh timestamped placeholder, the
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

Run the producer service again to create and publish a second placeholder. Do
not restart the mirror. Acceptance for mutation:

1. The mirror discovers a higher sequence and different info hash.
2. It pauses/removes the old torrent and resolves the new torrent metadata.
3. It force-rechecks the existing canonical file.
4. It resumes requests for changed pieces and reaches `seeding` again.
5. The mirror container uptime is continuous across the replacement.
6. `mirror_state.json` and `snapshot-meta.json` contain the second hash.

Also stop and start the producer seeder between two publications. Acceptance is
that the restarted seeder publishes or verifies a strictly current DHT sequence
without reusing a sequence for a different value, and the mirror still detects
the next update. Record producer DHT publication logs, mirror transition logs, both info hashes,
file sizes, peer/source counters, and the final seeding state. Do not describe
the transfer as P2P-proven unless the leecher receive/source counters and
producer upload/peer-transfer counters show peer-sourced bytes.

After the test, remove `USE_PLACEHOLDER_SNAPSHOT` or set it to `0`, restore the
production DHT salt, and trigger or wait for the normal production pipeline as
appropriate.
