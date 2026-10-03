# lexsys-log

A durable, segmented, append-only log written in [lex-sys](https://github.com/alpibrusl/lex-sys): no `Ffi`, no `unsafe`, and a
checkable authority report. The engine under a stream and queue server (Redis Streams over RESP first) and, later, a
[lex-trail](https://github.com/alpibrusl/lex-trail)-compatible event store.

**Status: design only. Nothing is built.** `docs/design.md` is the plan: the record format, the recovery invariant and the way it
will be tested, the commit policy, the pre-registered gate against Redis Streams, and what this will not be. The measurements in it
are of Redis and of this machine's disk; none are of this project yet.

It is **not** a Kafka replacement: one node, no replication, no consensus.

Licence: EUPL-1.2.
