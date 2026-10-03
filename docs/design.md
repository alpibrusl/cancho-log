# lexsys-log: a durable log in lex-sys

Status: **design only.** Nothing here is built. The numbers in section 2 are measurements of Redis 7.0.15 and of this machine's disk, not of this project. Sections 6 and 9 are the two the project is judged on: the recovery claim and the gate.

## 1. What this is for, and the claim it must survive

A log is the engine under three things people build separately: a **queue** (producers append, consumer groups read and acknowledge), a **stream** (readers replay from an offset), and an **audit trail** (an append-only record someone can verify later). This project builds the engine once and puts the first of those on it: **Redis Streams over RESP**, because the RESP parser, the connection loop and the differential harness against `redis-server` already exist in [`lexsys-cache`](https://github.com/alpibrusl/lexsys-cache), and an oracle that already works is the most valuable thing a new project can start with.

The claims, in the order they must be earned:

1. **Crash-correct.** After a crash at *any* byte, recovery yields a log that is a prefix of what was appended and contains every append that was acknowledged. This is tested by cutting and corrupting real files at every offset (section 6), not argued.
2. **Not slower than Redis where durability is the cost.** With Redis at `appendfsync always`, an acknowledged `XADD` costs a disk flush, and the claim is that this engine is within the pre-registered ratio of it (section 9). Where throughput is not flush-bound, no claim is made.
3. **A checkable authority report.** `lex-sys authority` on the server names one data directory, one port and a clock, and says nothing called `ffi`.

It is **not** a Kafka replacement. One node, no replication, no consensus: a machine that dies takes its disk's contents with it, and this document will say so in the README's first paragraph. It is also not a general database; there is no SQL, no query planner and no secondary-index language. The earlier question "should we build a DB?" had the answer *the storage engine, not the query layer*, and this is the engine.

## 2. Measurements this design rests on

All on this machine: 4 vCPU Xeon at 2.1 GHz, 16 GB, ext4 on a virtual disk, one writer. Each is a single measurement session unless it says otherwise.

**The flush, from `lex-sys/docs/file-writes.md` section 1.1 (128-byte records, 2,000 of them):**

| | per record |
|---|---|
| `write` only | 0.4 us |
| `write` + `fdatasync` after each | 193 us |
| `write` + `fsync` after each | 217 us |
| `fdatasync` every 100 records | 4.2 us |

**Redis Streams, `XADD s * f <100 bytes>`, `redis-benchmark -n 30000` against `redis-server` 7.0.15 with `--save ""` and the AOF on:**

| appendfsync | clients | pipeline | requests/s | p50 |
|---|---|---|---|---|
| always | 1 | 1 | **3,894 / 4,040 / 3,975 / 3,896** (four runs) | 0.21 ms |
| always | 50 | 1 | **44,843 / 34,404 / 48,465 / 40,053** (four runs) | 0.9-1.3 ms |
| always | 1 | 16 | 34,483 (one run) | 0.41 ms |
| everysec | 1 | 1 | 23,095 (one run) | 0.04 ms |
| everysec | 50 | 1 | 107,143 (one run) | 0.24 ms |
| no | 50 | 1 | 110,701 (one run) | 0.24 ms |
| no | 50 | 16 | 535,714 (one run) | 1.3 ms |

What these say, and what they do not:

* **`always` with one client is flush-bound and stable.** 3,900-4,040 requests a second is one flush per command (about 250 us each); the four runs agree within 4%. This is the cell where a log's whole job is visible: the engine adds almost nothing to the flush, so a ratio near 1.0 is the *expectation*, and a ratio well below it would mean the engine itself is slow.
* **`always` with fifty clients is group commit, and it is noisy.** Redis's design is to flush once per event-loop pass and reply to every command in it, so fifty clients can share each flush; the measured scaling (about ten times the one-client rate) is consistent with that, and it was not traced. 34,000-48,000 a second, a 41% spread across four runs. A median of at least five runs is the minimum for any claim here, and the spread is quoted with it.
* **Redis uses `fdatasync`, not `fsync`.** Under `strace` with `appendfsync always`, five `XADD`s produced eight `fdatasync` calls and one `fsync` call. lex-sys's `file_sync` is `fsync`, which on this disk is 217 us against 193 us, **11% more per flush**. In the flush-bound cells that alone puts this engine at about 0.89 of Redis before it does any work of its own. The gate (section 9) names this, and the remedy is `fdatasync` in lex-sys (listed in `file-writes.md` slice 3, to be built when a measurement asks for it, and this is that measurement), not engine work. Measured here with appends that extend the file, which is the case a log has, so the 193 us already includes the length update.
* The pipelined and `everysec`/`no` rows are Redis's own speed with the flush mostly out of the way. They are context, not a target: a single-threaded RESP loop in lex-sys served 1.07-2.00 times Redis in `lexsys-cache`'s gate, and nothing here is expected to differ in the parts that are not the flush.

**What is not measured:** this project, at all; recovery time per gigabyte; the cost of CRC-32C in lex-sys; the memory cost of the index. Section 10 lists each as a measurement to take with its own pre-registered question.

## 3. The model

**A stream is a sequence of entries with strictly increasing ids**, an id being `(ms, seq)`, two unsigned 64-bit integers, exactly as Redis defines them so that a client library sees no difference. An entry is a list of field/value byte-string pairs. `XADD` with `*` makes an id from the clock (`ms` from the wall clock, never below the last id's `ms`) and with an explicit id requires it to be greater than the last one; anything else is Redis's own error text.

**One log directory per stream.** The alternative is one physical log with a per-stream index, which is how a Bitcask-style store works and has one attraction: a thousand tiny streams cost one open file. It also makes retention hard: dropping the oldest data of one stream means compacting a log shared with all the others. With one directory per stream, retention is deleting whole sealed segments, which is one `fs_remove` and no rewriting, and `XTRIM` is cheap and honest (section 7). The price is that the number of streams is bounded by file descriptors (a few hundred open at once, the rest opened on demand). A queue server has few streams and a lot of entries, so the bounded side is the right one to bound. Open question 1.

**Segments.** A stream's directory holds segment files named by the id of their first entry, zero-padded (`00000000000000001234-0000000000000000.log`, so names sort as ids sort), plus a `MANIFEST`. A segment is **active** while it is being appended to and **sealed** once a newer one exists. The active segment is rolled at a configured size (default 64 MiB) or age. Sealed segments are immutable, which is what makes recovery tractable: only the *active* segment can have a torn tail.

**Why there is no directory listing.** `fs_list` does not exist in lex-sys and is not planned for this. A `MANIFEST` file names the segments in order and is replaced atomically (write a temporary, `file_sync` it, `fs_rename` it over the old, sync the directory), as LevelDB's `CURRENT` is. The active segment's name is derivable from the last sealed one's, and an orphan from a crash between creating a segment and publishing the manifest is found by asking for the one name that could exist. Section 6.3 enumerates the crash points this has to survive.

## 4. The record format

A record is a header and a body, little-endian, no padding:

| bytes | field | |
|---|---|---|
| 4 | `len` | bytes that follow this field: at least 24 (a record with no pairs is 28 bytes in all), at most the configured maximum (default 1 MiB), above which an append is refused |
| 4 | `crc` | CRC-32C of everything after this field |
| 8 | `ms` | the entry id's millisecond part, 0 to 2^63 - 1 |
| 8 | `seq` | its sequence part, the same range |
| 4 | `fields` | the number of field/value pairs |
| ... | pairs | each pair is `u32` length, bytes, `u32` length, bytes |

Ids are held in lex-sys's signed 64-bit `int`, so an id at or above 2^63 is refused, on write and on read. Redis's ids are unsigned; a millisecond clock is nowhere near 2^63, and an explicit `XADD` id above it is an error that says so, a departure listed in the README. The record layout above is implemented in `src/record.ls` and the CRC in `src/crc.ls`.

**What the checksum is for.** A torn write leaves a record whose length says it extends past the end of the file, or whose bytes are not what the checksum says. CRC-32C is the choice because it is cheap, is what other logs use for this exact job, and catches the failures that matter (a prefix of the record, a page of zeros, a page from an older file). It is not a defence against an adversary: whoever can write the file can write a matching checksum. Tamper-evidence is a separate layer (section 8).

**Reading is bounds-checked at every step.** A `len` below the minimum or above the maximum, a `fields` count that overruns `len`, a pair length that overruns the record: each is "this record is bad", never a trap. lex-sys's checked arithmetic and slice bounds are the second line, and lex-sys's rule that no input reaches a panic applies to a file as much as to a socket. A recovery that crashes on a damaged file would be the worst possible behaviour for a recovery.

**A sparse index** maps ids to file positions every N entries (default 4 KiB of log), one `.idx` per segment. It is **derived**: never trusted, rebuilt by scanning if it is missing or its own checksum is wrong, and a lookup that lands on a wrong position finds a bad record and falls back to a scan from the segment's start. An index that can be wrong without being noticed is a corruption source; one that can only slow a lookup down is a cache.

## 5. The commit policy

An append is **acknowledged** when a flush covering it has returned success. Nothing else counts.

* **`always`.** The server appends every command that arrived in this pass of its event loop, then flushes once, then replies to all of them. One client gets one flush per command (the flush-bound cell above); many clients share each flush, which is Redis's behaviour and the source of its 34,000-48,000 a second at fifty clients. This is the default, because a log that acknowledges before it is durable is a different product.
* **`interval <ms>`.** Replies go out before the flush; a flush follows when the interval has elapsed. Redis's `everysec`. The loss window is the interval, and it is the *only* policy where an acknowledged append can be lost; the INFO output says which policy is on.
* **`never`.** For benchmarks and tests. Named so, and refused unless asked for.

**A failed flush is not retried.** If the flush returns an error, the file's contents are unknown (`file-writes.md` section 5.1: the kernel may have dropped the dirty pages and cleared the error, so a second flush can succeed over data that was never written). The server stops acknowledging, replies with an error to everything pending, and refuses further appends until it has been restarted and has recovered from what is on disk. It does not retry, and it does not carry on as if nothing happened.

**The flush blocks the loop.** One thread, one flush at a time, about 200 us on this disk, during which no other connection is served. That is the design's honest cost and it is bounded; `max_turn_ms` is reported as it is in `lexsys-cache`. Moving the flush to its own thread is possible (lex-sys has `spawn`/`join` and forked heaps) and is not in the first version, because a flush that blocks only the loop is simpler to reason about than one that races it, and the measurement that would justify the complexity does not exist yet.

**Preallocation** (extending a file in large steps so a flush need not also journal a length change) is how some logs make `fdatasync` cheaper. lex-sys has no `fallocate`, the 193 us above already includes the length update, and nothing here depends on it. Not planned.

## 6. The recovery invariant, and how it is tested

### 6.1 The claim

> After any crash, recovery produces a log whose entries are a prefix of the entries that were appended, in order, and **that prefix contains every entry that was acknowledged.**

"Any crash" means the process dying at any point, and the file system keeping any prefix of the bytes written since the last successful flush, in any combination of pages. It does not mean a lying disk: a device that acknowledges a flush it has not performed breaks this claim and every other log's.

The argument for why it holds: an entry is acknowledged only after a flush that covers it, and a flush makes everything before it durable. So every acknowledged entry is below the durable point. Anything *above* the durable point was never acknowledged and may be lost in any pattern, including a pattern where a later page survives and an earlier one does not. Recovery therefore **truncates at the first bad record in the active segment**, and the truncation can lose only unacknowledged entries, because a bad record cannot lie below the durable point.

That argument depends on one thing recovery must not do: **repair damage below the durable point as if it were a torn tail.** A bad record in a *sealed* segment is not a crash artefact. It is corruption, and recovery refuses to open the stream and says which record. Refuse, do not downgrade: truncating a sealed segment would silently turn bit rot into data loss that nobody was told about.

### 6.2 The test, at every byte

The harness is real files and the real binary, because the property is about files:

1. A workload (a seeded random run of appends, rolls and flushes, plus a model of what was acknowledged when) produces a directory.
2. For **every prefix length** of the active segment, copy the directory with the segment cut there, run recovery, and check the claim against the model: a prefix of the appended entries, in order, every acknowledged entry present.
3. For **every page** (512-byte and 4 KiB alignment) in the unflushed region, copy the directory with that page zeroed and with that page filled from a different offset, and run recovery. This is the case of a later page surviving an earlier one, which a prefix cut cannot produce and which is the real-world shape of a torn write.
4. For **every byte** of a sealed segment, flip a bit and require the stream to be **refused**, not repaired and not opened.

The cost is the number of offsets times a recovery, which for a few-kilobyte log is thousands of runs of a program that starts in milliseconds. The size of the log in the test is chosen so the whole sweep runs in CI.

**Mutation testing** of the recovery code, in a scratch copy and never committed, as in every earlier project here: recovery that keeps a bad record, truncates one record too many, trusts the index, ignores a bad checksum in a sealed segment, or treats a zero-length record as the end. Each must fail a test; a survivor is either a missing test or an equivalent mutant, and which is written down.

### 6.3 The directory-level crash points

Rolling a segment and replacing the manifest are the operations where the *directory* is in an intermediate state. The harness enumerates them by name, and each has a defined recovery:

| crash after | on disk | recovery |
|---|---|---|
| the new segment is created, nothing else | an empty segment not in the manifest | found by asking for the next name; removed if empty, refused if it holds records the manifest does not know about |
| the new segment is created and synced, the manifest is not | as above, with a valid empty file | as above |
| the manifest temporary is written, not renamed | a stray `MANIFEST.tmp` | removed; the old manifest stands |
| the manifest is renamed, the directory is not synced | either the old or the new manifest, not a mixture (`rename` is atomic) | whichever is there is consistent with the segments, by construction |
| the old segment's last flush, then the roll | the old segment complete | sealed by the manifest; its length is checked against it |

Whether `rename` is atomic across a crash is POSIX's guarantee and not measured here. The harness cannot cut a rename in half, so it checks what recovery does in *each state* the guarantee allows, and a file system that does not honour the guarantee is outside the claim.

### 6.4 What this cannot show

That an acknowledged flush survives a power cut. A process-level test cannot do it, and nothing in this repository can. The claim is about what recovery does with the bytes that are there; that the bytes are there is the disk's.

## 7. The Redis Streams front-end

**v1 implements:** `XADD` (with `*` and explicit ids; `MAXLEN` and `MINID` with `~`), `XLEN`, `XRANGE`, `XREVRANGE`, `XREAD` (with `COUNT` and `BLOCK`), `XDEL` (as a tombstone record, see below), `XTRIM`, `XINFO STREAM` (the fields a client library reads), and the handshake commands `lexsys-cache` already answers (`HELLO`, `CLIENT`, `INFO`, `CONFIG GET`, `QUIT`).

**Consumer groups:** `XGROUP CREATE`/`DESTROY`/`SETID`, `XREADGROUP`, `XACK`, `XPENDING` (summary and extended), `XCLAIM` and `XAUTOCLAIM`. A group's state (the last delivered id, and for each consumer its pending entries with delivery count and time) must survive a crash, because a queue that forgets what it has delivered re-delivers everything.

**Group state is a second log.** Each change (a delivery, an acknowledgement, a claim) is a record in the group's own small log, and the state is rebuilt by replaying it. The log is compacted into a snapshot by writing a new one, syncing it and renaming it over the old, the same manifest discipline as section 3. This is where `fs_rename` and `file_lock` earn their place, and why the two were built before this document.

**Trimming is by whole segment.** `XTRIM MAXLEN ~ n` and `MINID ~` drop sealed segments that are entirely past the limit, which is what Redis's `~` already means (it trims in whole macro-nodes). **Exact trimming (`XTRIM MAXLEN n` without `~`) is not supported in v1** and answers an error that says so; it would mean rewriting a segment to cut part of it. `XDEL` of a single entry writes a tombstone and the entry disappears from reads, but its bytes stay until its segment is dropped. Both are honest departures from Redis and are in the README's list of differences.

**Not in v1:** `MULTI`/`EXEC`, `WAIT`, scripting, the other Redis data types, TLS, more than one database. Replication is not on the list at all.

**The differential harness** is `lexsys-cache`'s, pointed at streams: the same command sequence is sent to `redis-server` and to this server and the replies compared byte for byte. Anything non-deterministic (`*` ids, times) is compared by shape and the explicit-id forms carry the byte-exact weight. It is also where the departures above are pinned: each is a test that the reply is the documented one and not Redis's.

## 8. Trail mode: lex-trail events, and what a chain does and does not prove

[`lex-trail`](https://github.com/alpibrusl/lex-trail) is an event log: an event is `(kind, parent, payload_json, ts_ms)` and its id is `sha256(kind NUL parent NUL payload NUL ts_ms)`, in the format its `SPEC.md` fixes with test vectors. Its backends are in memory and SQLite. A durable, fast backend for the same events is a natural second use of this engine, and what it needs is small:

* **Append by id, idempotently.** Re-appending an identical event is a no-op, not an error (`INSERT OR IGNORE` in the SQLite backend). This needs an in-memory id index, rebuilt by scanning the log on open.
* **Verify on append and on read.** The stored id must equal the recomputed one. A record whose id does not match is bad in the sense of section 4.
* **Queries** `lex-trail` offers: by id, by time range, by `task_id` (a field *inside* the payload JSON), and the chain of `parent` pointers. The `task_id` index needs the payload parsed on append (`std.json`'s tape parser is the tool) and is, like the sparse index, derived and rebuilt.

**What the chain proves, and what it cannot.** `lex-trail`'s own `SPEC.md` is exact about this and so is `lex-os-audit`: an id detects an edited payload, and an anchor detects a changed set of events, but **truncation of the tail is invisible** to anything inside the log, and a holder who recomputes every id after an edit hands over something that verifies. The signed seals and checkpoints in `lex-os-audit` exist to close those, and a checkpoint is only worth anything held somewhere the log's owner cannot reach. This engine adds durability to a trail and does not add to what the trail proves: it will not claim tamper-evidence beyond `lex-trail`'s, and trail mode inherits `lex-trail`'s limits, stated in its README the way `lex-trail` states them.

Trail mode is stage L4 (section 11), after the queue works. It is in this document so that the record format (section 4) does not have to change to hold it: a trail event is a record whose pairs are the event's four fields, and nothing in the engine assumes a record is a Redis entry.

## 9. The gate, fixed before the code

**Correctness comes first, and has no ratio.** All of these pass before any throughput number is quoted:

1. The recovery sweep of section 6.2, over prefixes, torn pages and sealed-segment bit flips, on both backends.
2. The directory crash points of section 6.3.
3. The differential run against `redis-server` for every implemented command, and the documented departures pinned.
4. A fuzz run: hostile bytes on the socket, and hostile bytes in a log file at startup. Neither may reach a trap.
5. Mutation testing of recovery, the record reader and the group-state replay, with every survivor classified.
6. `lex-sys authority` on the server: one data-directory prefix, one port bound, a clock, **no `ffi`**. Pinned by a test, so a drift in the report is a red build.

**Performance, pre-registered.** The comparison is `XADD` of 100-byte values against Redis 7.0.15 with `appendfsync always`, one writer core each, the median of at least five runs interleaved with Redis in one session, on this machine:

| cell | Redis baseline (section 2) | criterion |
|---|---|---|
| 1 client, no pipeline | about 3,900-4,040 requests/s | **at least 0.9 times Redis** |
| 50 clients, no pipeline | 34,000-48,000 requests/s, a 41% spread | **at least 0.9 times Redis**, with the spread of both quoted |

If either cell misses, the result says which and by how much, and the README does not say "as fast as Redis".

**The fdatasync caveat is pre-registered.** `file_sync` is `fsync`; Redis uses `fdatasync`, and the difference measured here is 11% per flush (section 2). In the 1-client cell that alone predicts a ratio of about 0.89, **just under the criterion, before the engine does anything**. If that cell misses by about that amount, the finding is *"fsync costs 11% on this disk and fdatasync is slice 3 of `file-writes.md`"*, not an engine problem, and the cell is re-run with `fdatasync` once it exists. It is written down now so that the miss, if it comes, is not explained after the fact. If the cell misses by much more than 11%, the engine is the cause and the cell says so.

**Reported, not gated:** `interval` and `never` throughput; reads (`XRANGE`/`XREAD` over a pre-filled log, from the page cache); 1 KiB values; `max_turn_ms` and the worst flush; recovery time per gigabyte of log; memory per stream and per group.

**Not compared:** more than one core. Redis scales by processes and sharding; a log that shares one directory across threads needs a communication primitive lex-sys does not have (`parallelism.md`, T5). One core against one core is the only fair claim.

## 10. What is not measured yet, and what each measurement will decide

| not measured | decides |
|---|---|
| ~~CRC-32C throughput in lex-sys~~ **measured, section 10.1** | |
| recovery time per gigabyte of active-plus-sealed log | whether startup scans every segment or trusts sealed-segment sidecars, and what "start in seconds" costs |
| the cost of building the id index on open (trail mode) | whether the index is persisted or rebuilt, and at what log size that flips |
| a flush on its own thread against on the loop | whether the loop's flush stall is worth a second thread |
| memory per stream, per group, per pending entry | the real limit on how many streams and groups a process can hold |

### 10.1 CRC-32C, measured

A table-driven CRC-32C in lex-sys (`src/crc.ls`, a 256-entry table computed at compile time, checked arithmetic, `--backend llvm`), over a buffer of `size` bytes repeated, one core of this machine, `bench/crc_speed.ls`:

| record size | rounds | ns per record | MB/s |
|---|---|---|---|
| 100 B | 2,000,000 (three runs) | 218 / 206 / 204 | 459 / 484 / 490 |
| 1,000 B | 200,000 | 2,446 | 409 |
| 4,096 B | 50,000 | 10,181 | 402 |

About 200 ns for a 100-byte record, and about 410-490 MB/s. The answer to the question this row asked: **the checksum is not a visible part of an append.** Against a flush of about 200,000 ns it is 0.1%; with the flush out of the way (`interval` or `never` at 100,000 appends a second) it is about 2% of a core. A slicing-by-8 table is not needed and is not planned. The figure includes the benchmark's loop and a modulo per round, so it slightly overstates the checksum.

## 11. Plan, and what each step needs from lex-sys

| step | builds | needs from lex-sys |
|---|---|---|
| **L0** | the record format and CRC-32C (**built**: `src/crc.ls`, `src/record.ls`, tests and mutation checked), then the segment and manifest code, recovery, and the byte-by-byte sweep (section 6). No network. | `file-writes.md` slices 1 and 2: **built** |
| **L1** | the RESP loop (copied from `lexsys-cache` with attribution, to be extracted as a package once two projects use it), `XADD`, `XLEN`, `XRANGE`, `XREAD`, the handshake, the differential harness | nothing new |
| **L2** | consumer groups: the group log, snapshotting by rename, `XREADGROUP`, `XACK`, `XPENDING`, `XCLAIM` | nothing new |
| **L3** | trimming, `XDEL` tombstones, the `interval` policy, the measurements of section 10 | `fdatasync` if the gate's caveat bites |
| **L4** | trail mode: id index, verify on read, the `lex-trail` vectors as a test | nothing new |

An OpenTelemetry front-end, if it is wanted, is an adapter in front of L4's trail mode (OTLP/HTTP, a span's parent as the `parent` link, `trace_id`, `span_id` and `unix_nano` in the payload because `lex-trail` keeps milliseconds), after the rest works. It is not in the engine and not in this plan.

## 12. Open questions

1. **One directory per stream, or one shared log with per-stream indexes?** Section 3 chooses the directory because retention is deleting a file, and bounds the number of streams by file descriptors. A workload with thousands of tiny streams would want the other design. Is that a workload this is for?
2. **Is `interval` worth shipping in v1,** given that it is the one policy where an acknowledged append can be lost? It is what makes Redis fast by default and what makes it surprising, and the INFO line is the only guard.
3. **Where does the RESP parser live?** Copying it from `lexsys-cache` is the quick answer and means two copies of a parser; extracting a package is the right one and is a lex-sys `vcs publish` exercise of its own.
4. **Does exact `XTRIM` matter?** Section 7 refuses it. Redis users mostly use `~`, but a client that sends the exact form gets an error, not a trim.
