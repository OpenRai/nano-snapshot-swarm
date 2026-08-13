# Public beta operator runbook

This is a beta distribution system. A mirror operator contributes disk, upload
bandwidth, and a reachable BitTorrent port. Availability, peer discovery, and
update latency are not guaranteed.

## Start a mirror

Use a persistent host directory. The image has the producer signing key and DHT
salt baked in, so the normal command needs no secret environment variables:

```bash
mkdir -p ./nano-data
docker run -d --name nano-mirror \
  -p 6881:6881/tcp -p 6881:6881/udp \
  -v "$PWD/nano-data:/data" \
  --restart unless-stopped \
  ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest
```

Use `-e POLL_INTERVAL=60` for a manual test. The production default is 600
seconds. Do not share `/data` between mirror containers.

## Verify steady state

```bash
docker logs --tail 100 nano-mirror
cat ./nano-data/mirror_state.json
```

Healthy steady state has `phase: "seeding"`, `progress: 1`, and a running
container. The five-minute `Seeding | ...` heartbeat is expected even when
there are no peers. `Peers` means established BitTorrent peers; a queued
seed-peer connection is not an established peer.

The full producer signing public key is copyable. Routine DHT target IDs and
torrent v2 info hashes are internal diagnostic IDs and are abbreviated in logs;
the complete values remain in JSON state and dashboard responses.

## Updates and restarts

The mirror polls the signed DHT mutable item. On a higher sequence it pauses the
old torrent, resolves metadata, rechecks the canonical archive, downloads the
new pieces, and returns to `seeding` without a container restart. Restarting the
container with the same `/data` restores DHT and torrent state and falls back to
a safe recheck when resume data is missing, rejected, or corrupt.

The producer has a separate long-lived seeder. Its `seeder-stats.json` is the
producer health source:

```bash
cat /path/to/nano-snapshots/seeder-stats.json
```

For a ready producer, `state` is `seeding`, `dht_verified` and `seeder_ready`
are true, `torrent_info_hash` equals `dht_verified`'s hash, and
`dht_sequence` is present. `dht_direct_acknowledgements` is a diagnostic count,
not a substitute for authoritative read-back.

## No-peer troubleshooting

`No peers` is not itself a failed download. Check, in order:

1. The DHT discovery log reports the expected signed sequence and info hash.
2. The mirror is listening on both TCP and UDP 6881.
3. The configured producer seed peer is reachable from the mirror network.
4. The mirror's `progress` is changing or the producer has upload activity.
5. The producer and dashboard advertise the same torrent v2 info hash.

Queued connection messages only mean that libtorrent accepted an asynchronous
request. Look for an established peer event or `Peers: N` before claiming a
peer path is working.

## Upgrade and report a problem

```bash
docker pull ghcr.io/openrai/nano-snapshot-swarm/nano-p2p-mirror:latest
docker rm -f nano-mirror
# rerun the start command with the same /data directory
```

Keep `mirror_state.json`, the relevant container logs, the image digest, and the
timestamps of the affected DHT discovery cycles when reporting a problem. Do
not include private keys or other host secrets.
