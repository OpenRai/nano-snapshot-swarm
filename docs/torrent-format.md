# Torrent and Magnet Format

This is the canonical reference for what the current snapshot torrent and
magnet represent, and why the repository keeps each part. The implementation is
the final authority: use the source links below when this document and the code
disagree.

For protocol mechanics, use the specifications and library reference: [BEP 9
(metadata exchange and magnet URIs)](https://www.bittorrent.org/beps/bep_0009.html),
[BEP 46 (DHT mutable items)](https://www.bittorrent.org/beps/bep_0046.html),
[BEP 52 (BitTorrent v2)](https://www.bittorrent.org/beps/bep_0052.html), and
[libtorrent torrent creation](https://libtorrent.org/reference-Create_Torrents.html).

## What is published

The current snapshot is one canonical archive:

```text
nano-ledger-snapshot.7z
```

two identifiers for the same torrent:
The producer creates a hybrid v1+v2 `.torrent` for that archive. It exposes two
identifiers for the same torrent:

- `info_hash_v1`: the v1 compatibility identifier.
- `info_hash`: the v2 identifier and the repository's canonical DHT/API key.

The upstream filename is not the torrent name. It is retained only in
`x-snapshot.original_filename`, so operators can identify the source archive
without changing the canonical on-disk or published name.

The torrent includes the configured public trackers and the `x-snapshot`
provenance metadata. It does not include a webseed or an HTTP snapshot URL.
The producer's outer creation comment is available from a `.torrent` file but
is not part of the magnet representation.

The implementation that creates this object is
[`create_torrent()`](../producer/torrent_create.py).

## Why there are two hashes

The hybrid torrent lets v1-compatible and v2-capable BitTorrent clients refer to
the same archive. The v2 hash remains the DHT value because this repository's
mutable snapshot record already carries a raw 32-byte v2 pointer; changing that
record would be a separate compatibility migration.

The v1 hash is therefore carried through status as optional compatibility data,
not used as a second snapshot pointer. If the two hashes appear beside one
another, they identify one hybrid torrent and must resolve to the same archive.

The raw-value boundary is
[`build_dht_value()`](../shared/bep46.py).

## What the magnet means

The magnet is the client-discovery representation of the same hybrid torrent.
It carries the optional v1 identity, the always-present v2 identity, the
canonical display name, the configured public tracker hints, and the optional
direct peer hint used by this deployment. The two `xt` values are alternate
identifiers for one torrent, not two snapshots. There is no webseed identity
because the system does not publish a webseed.

The implementation that assembles this URI is
[`_build_magnet()`](../status-api/app/main.py).

## What the public routes mean

The Status API stores the current torrent bytes under the v2 hash and exposes
three intentionally different meanings:

| Route | Meaning | Why it exists |
|---|---|---|
| `/api/torrents/{v2-infohash}/{torrent_name}.torrent` | The immutable torrent for one v2 hash | A client can cache it safely because the path names its content identity. |
| `/api/torrent` | The current/latest compatibility entry point | Older clients can keep using one URL; the response redirects with `307` and `Cache-Control: no-store`. |
| `/api/latest.magnet` | The current magnet as plain text | Scripts and operators can fetch the latest discovery information without parsing dashboard HTML. |
| `/nano-snapshot-swarm.producer-signing-pubkey.txt` | The current producer signing Ed25519 public key as plain text | Mirrors and operators can fetch the verification key without copying it from the dashboard or repository. |

The named route returns the canonical `Content-Disposition` filename
`nano-ledger-snapshot.7z.torrent`. The latest route is deliberately not
cacheable; the redirect's query parameter carries the current v2 hash so the
immutable route can change on each publish.

## Why a mirror can appear stale

The mirror follows the signed DHT v2 pointer, not the upstream filename and not
the torrent comment. For a newly discovered v2 hash it keeps the existing
canonical archive, adds the replacement paused, waits for metadata, rechecks
the archive, and then resumes piece requests. That update path is why matching
pieces can be reused without inventing an external chunk format.

For a download that is not fetching the current snapshot, compare these values:

1. `/api/status.sequence` and `/api/status.info_hash`.
2. The v2 `xt` in `/api/latest.magnet`.
3. The v2 hash in the `/api/torrent` redirect location.
4. The mirror's `last_seq`, `last_info_hash`, and phase in `mirror_state.json`.

If those agree, inspect metadata resolution, the `checking_files` phase, and
peer connectivity. A cached latest response, an upstream filename, or a v1 hash
by itself is not evidence of a newer snapshot.

The mirror's magnet/file loading boundary is
[`LibtorrentSession.add_torrent()`](../mirror/libtorrent_session.py).
