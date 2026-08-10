# Mutable-torrent interoperability research

## Conclusion

The production publisher and mirror should remain on the Mainline BitTorrent
DHT. The current design publishes a signed mutable pointer to a BitTorrent v2
infohash and works with this project's v2 torrent and mirror implementation.
Neither `dmt`, `dhtup`, nor HyperDHT provides a reason to replace it.

The current value format is a project-specific v2 extension of the BEP 46
pattern, not wire-compatible BEP 46. Change it only if interoperating with
third-party BEP 46 consumers becomes a stated requirement.

## Current implementation

`producer/torrent_create.py` creates v2-only torrents. `shared/bep46.py`
publishes the raw 32-byte v2 infohash as the mutable item's value. Libtorrent
bencodes and signs that value for the BEP 44 mutable-item protocol.

The publisher derives the mutable-item target from the Ed25519 public key and
the configured salt. The seeder republishes the active value every 30 minutes.
Mirrors know the authority public key and salt, verify and consume the same
value format, then join the referenced v2 torrent swarm.

This preserves the required BEP 44 properties:

- The value is signed by the authority key.
- Updates are sequence-numbered by libtorrent.
- The active item is republished while the seeder runs.
- A mirror needs the authority public key and salt before querying the DHT.

## BEP 46 comparison

BEP 46 is a draft titled *Updating Torrents Via DHT Mutable Items*. It defines
the mutable value `v` as a bencoded dictionary with an `ih` field containing a
20-byte BitTorrent v1 infohash. A consumer periodically gets that mutable item
and switches to the referenced torrent when it changes.

The project instead stores a raw 32-byte v2 infohash. That is valid as a BEP 44
mutable value, but it is not the BEP 46 payload shape. A generic BEP 46 client
that expects `v = {"ih": <20-byte hash>}` cannot consume the current stream.

This is intentional and appropriate for v2-only torrents. Do not replace the
production stream merely to match a draft whose payload cannot express a
v2-only infohash.

## dmt

`lmatteis/dmt` is the original reference implementation linked from BEP 46. It
is a small, historical WebTorrent command-line implementation. Its publisher
places an `ih` field in the bencoded mutable value and its source marks salt
handling as missing.

It confirms the original v1 BEP 46 convention but does not add a reliability,
security, or maintenance capability beyond the project's libtorrent-based
implementation. It is not a dependency or migration target.

## dhtup

`getlantern/dhtup` is a Go library and application built on the maintained
anacrolix DHT and torrent libraries. It resolves a mutable item, bdecode-parses
the result as an anacrolix `Bep46Payload`, and then downloads the referenced
torrent. It can add web seeds, trackers, and metainfo URLs as download
fallbacks.

It is useful independent evidence of the conventional BEP 46 payload format.
It will not read the current raw v2 value, because it expects the bencoded
`Bep46Payload`. Its source also records a future need to persist sequence state
for update detection; the project's mirror already persists and compares the
observed sequence number.

## HyperDHT and Hyperswarm

HyperDHT is a separate DHT network and protocol. It provides:

- signed mutable and immutable records;
- keyed peer lookup and announcements;
- UDP hole punching and relays; and
- encrypted direct connections.

Hyperswarm is the higher-level peer-discovery and connection-management layer
on top of HyperDHT. Neither is a Mainline BitTorrent DHT implementation, and
neither defines a BEP 46 mutable-torrent convention. HyperDHT mutable records
have a key pair, value, signature, and sequence number, but their records and
bootstrap network are not shared with libtorrent's Mainline DHT.

HyperDHT could carry a v2 infohash in a separate application protocol, but it
would require HyperDHT-aware publishers and mirrors. It would not improve
interoperability with BitTorrent clients or the current swarm. It is therefore
out of scope for the production publisher.

## Decision and conditional future work

Keep the current publisher, seeder republish interval, v2 torrent format, and
raw 32-byte pointer value unchanged.

If third-party BEP 46 consumption becomes a requirement, add a separate
compatibility stream rather than changing the existing one:

1. Create a hybrid or v1 torrent and retain its 20-byte v1 infohash.
2. Publish a second mutable item, under a distinct documented salt.
3. Encode its value as the canonical bencoded `{ih: <20-byte v1 infohash>}`
   payload.
4. Test it with an independent consumer such as `dhtup`.

The compatibility stream would be an additional product surface. It should not
be introduced without a concrete consumer.

One future correctness improvement is independent of interoperability:
`producer/publish.py` reports a locally predicted sequence while libtorrent
chooses the sequence used for the DHT put. If that short-lived publisher becomes
the operational path, record the sequence from the put result before treating
it as authoritative status data.

## Sources

- [BEP 46: Updating Torrents Via DHT Mutable Items](https://www.bittorrent.org/beps/bep_0046.html)
- [BEP 46 background discussion](https://github.com/bittorrent/bittorrent.org/issues/34)
- [dmt reference implementation](https://github.com/lmatteis/dmt)
- [dhtup](https://github.com/getlantern/dhtup)
- [HyperDHT documentation](https://docs.pears.com/reference/building-blocks/hyperdht/)
