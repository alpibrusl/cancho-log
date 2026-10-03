# lexsys-log

[![ci](https://github.com/alpibrusl/lexsys-log/actions/workflows/ci.yml/badge.svg)](https://github.com/alpibrusl/lexsys-log/actions/workflows/ci.yml)

A durable, segmented, append-only log, written in [lex-sys](https://github.com/alpibrusl/lex-sys): no `Ffi`, no `unsafe`, and a
checkable authority report.

It is the engine meant to sit under a stream and queue server (Redis Streams over RESP first) and, later, a
[lex-trail](https://github.com/alpibrusl/lex-trail)-compatible event store. Today it is a library of five source files that
lets a program **append a record, flush, and be sure that after a crash everything it was told was flushed is still there**,
and that a torn tail is cut off rather than believed.

It is **not** a Kafka replacement: one node, no replication, no consensus.

## Status

**L0, the append path built.** Done: the checksum (`src/crc.ls`), the record format (`src/record.ls`), the scan and recovery of
a segment (`src/segment.ls`), and the append path (`src/log.ls`: append, flush, read-back, group flush). Each is tested from
bytes and by a crash sweep against an independent reader. Not yet: rolling segments, the manifest, an index, consumer groups,
the Redis Streams front-end, trail mode. [`docs/design.md`](docs/design.md) is the plan; its measurements are of Redis, of
the disk and of the checksum, not yet of this engine.

## Requirements

- The **lex-sys** compiler, at the revision this repository's CI builds and tests with (below). A source file does not record
  the `std` it was written against, so the revision is part of the contract.
- Rust, to build that compiler (its `rust-toolchain.toml` pins the toolchain).
- `python3`, for the crash sweep.

## Quick start

```sh
git clone https://github.com/alpibrusl/lex-sys
git clone https://github.com/alpibrusl/lexsys-log && cd lexsys-log

REV=$(sed -n 's/^ *LEX_SYS_REV: *//p' .github/workflows/ci.yml)    # the revision CI uses
(cd ../lex-sys && git checkout "$REV" && cargo build --release -p lex-sys)
export LEX_SYS=$PWD/../lex-sys/target/release/lex-sys

# logtool is the small command line the crash sweep drives: it writes, dumps and recovers one segment
mkdir -p build
$LEX_SYS build src/logtool.ls src/log.ls src/segment.ls src/record.ls src/crc.ls --std -o build/logtool

mkdir /tmp/demo && build/logtool write /tmp/demo 5 2 1     # append 5 records, flush every 2
build/logtool dump /tmp/demo                               # read them back through the log
build/logtool recover /tmp/demo active                     # scan the segment; cut a torn tail
```

`write` prints each record's end offset (`rec <n> <end>`) and each flush (`sync <offset>`), so a test knows exactly what was
acknowledged; `dump` prints `r <id> <seq> <size>` for every durable record; `recover` prints the verdict (`0` clean), where the
valid data ends, and how many records it holds.

## Examples

**Use the log from a program.** This is the shape [`lexsys-hooks`](https://github.com/alpibrusl/lexsys-hooks) uses (see
`open_log` and its `POST /events` handler in `src/hooks.ls` there for the whole, working version). The caller opens the files,
so the log needs no capability of its own:

```
// Open: three handles to one file, then recover it (cut a torn tail), then attach.
//   open_append(fs, path) -> w      open_rw(fs, path) -> rw      open_read(fs, path) -> rd
let rec = log.recover(rw, window, max_len);       // (status, valid_end, records, last_ms, last_seq)
let lg  = log.attach(w, rd, rec.1, rec.2, rec.3, rec.4, max_len);

// Append: build a record (one pair here), write it, and only then flush.
let p     = record.begin(scratch, 0, ms, 0, 1);               // id `ms` must exceed the last one
let end   = record.put_pair(scratch, p, "event", body);
let total = record.seal(scratch, 0, end);
log.append(lg, scratch[0..total], ms, 0);                     // 0 = ok; 1 id not after, 2 too long, 3 broken
if log.flush(lg) == 0 { /* now, and only now, acknowledge the append */ }

// Read back what a flush has covered.
let r = log.read_at(lg, offset, window);                      // (0, size) | (1, 0) end | (2, 0) not a record
```

Several appends can share one `flush` (group commit); that is the whole reason the two are separate.

**A failed write is never retried.** After a failed `append` or `flush` the log marks itself broken and refuses everything until
it is reopened and recovered, because the file's contents are then unknown. A caller that gets a nonzero code answers its
clients with an error, never an acknowledgement.

## The record

```
len u32 | crc u32 | ms u64 | seq u64 | fields u32 | pairs...        pair = key_len u32, key, value_len u32, value
```

`crc` is CRC-32C (Castagnoli) over everything after `len`; `(ms, seq)` is the record's id and must increase. Recovery keeps the
longest prefix of whole, valid records of the *active* segment and refuses a damaged *sealed* one. The invariant, how it is
tested, and the choices behind the format are in [`docs/design.md`](docs/design.md) sections 4 to 6.

## Tests

```sh
$LEX_SYS test tests/crc_test.ls src/crc.ls --std                        # CRC-32C: the RFC 3720 vectors and properties
$LEX_SYS test tests/record_test.ls src/record.ls src/crc.ls --std       # the record format, from bytes
python3 tests/sweep.py build/logtool                                    # the crash sweep, against an independent reader
```

The sweep cuts a log at every byte, tears blocks and zeroes their tails, flips bits in sealed segments, and compares what recovery
keeps with a reader written separately in Python (with its own CRC-32C). It has been mutation-checked: deliberately broken
copies of the log are killed by it, and the two that survive are documented as unobservable in
[`docs/design.md`](docs/design.md) section 12. `bench/crc_speed.ls` measures the checksum.

## Documentation

- [`docs/design.md`](docs/design.md): the claim, the measurements it rests on, the record format, the commit policy, the
  recovery invariant, the planned Redis Streams front-end and trail mode, the gate fixed before the code, and what is not
  measured yet.

## Layout

```
src/crc.ls       CRC-32C
src/record.ls    the record: build, seal, check, read fields
src/segment.ls   scan a segment and decide what survives
src/log.ls       the open log: append, flush, read_at
src/logtool.ls   the command line the sweep drives
tests/           unit tests (lex-sys) and the crash sweep (Python)
bench/           the checksum's speed
docs/design.md   the plan
```

## Limitations

One segment, no manifest, no index (reads are by offset), no consumer groups, no network front-end. Reads see only what a flush
has covered. Ids are 0 to 2^63-1 and must increase within a log.

## Contributing

Every change goes through the same steps CI runs: `$LEX_SYS fmt --check src tests bench`, the two unit suites and the crash
sweep above. Design before code, in `docs/`, with claims measured; a claim that turns out false is corrected in place.

## Licence

[EUPL-1.2](LICENSE).
