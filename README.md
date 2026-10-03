# lexsys-log

A durable, segmented, append-only log written in [lex-sys](https://github.com/alpibrusl/lex-sys): no `Ffi`, no `unsafe`, and a
checkable authority report. The engine under a stream and queue server (Redis Streams over RESP first) and, later, a
[lex-trail](https://github.com/alpibrusl/lex-trail)-compatible event store.

**Status: L0, recovery built.** The checksum (`src/crc.ls`), the record format (`src/record.ls`), and the scan and recovery of a segment (`src/segment.ls`) exist, with 16 + 7 tests from bytes and a crash sweep (`tests/sweep.py`, 8,662 checks against an independent reader in Python, mutation checked). Rolling segments, the manifest and the append path with a group flush do not yet. `docs/design.md` is the plan: the record format, the recovery invariant and the way it
will be tested, the commit policy, the pre-registered gate against Redis Streams, and what this will not be. The measurements in it
are of Redis, of this machine's disk, and of the checksum; none are of the engine yet.

It is **not** a Kafka replacement: one node, no replication, no consensus.

Licence: EUPL-1.2.
