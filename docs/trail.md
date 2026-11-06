# Trail mode

Trail mode stores [lex-trail](https://github.com/alpibrusl/lex-trail) events in this log. It is `docs/design.md` section 8 built, in
the narrow form the first user needs: an **append-only, hash-chained, linear evidence log** whose ids are lex-trail's, so that any
lex-trail verifier can recompute them. Each claim here is **measured** (with the command), a **choice**, or **open**.

## 1. What is stored

An event is `(kind, parent, payload, ts_ms)`; its id is `hex(sha256(kind NUL parent NUL payload NUL decimal(ts_ms)))`, the
payload hashed as text. That is lex-trail's `SPEC.md` section 1, and **measured**: `tests/trail_vectors_test.cho` reproduces all six
event ids and all four anchor digests of lex-trail's `spec/vectors.json` (generated from that file by
`tests/gen_trail_vectors.py`, nothing computed by hand):

    cancho test tests/trail_vectors_test.cho src/trail.cho src/record.cho src/crc.cho src/log.cho src/segment.cho --std   # 10 passed

An event is one record (design section 4) with five pairs, in this order and no other: `id`, `kind`, `parent`, `payload`, `ts_ms`
(decimal text). The record's own id `(ms, seq)` is **derived**, not chosen: `ms = max(ts_ms, previous ms)`, and `seq` counts the
records that share an `ms`. An event stamped earlier than the one before it is accepted, takes the earlier one's millisecond, and
the log's ids still strictly increase (design section 4's rule). A record whose id is not the derived one is refused on read.

**Choices** (each is a refusal with a code, not a silent repair):

* `kind` is not empty and holds no NUL; `payload` holds no NUL (`SPEC.md` asks implementations to refuse these rather than rely on
  the separator); `parent` is empty or 64 lowercase hex digits; `ts_ms` is in `[0, 10^18)`, canonical decimal, so it fits a record id.
* The payload is **not** parsed. It is hashed as the text it is, as the spec says; an engine that validated it would have to
  decide what to do with a payload that is not JSON, and lex-trail does not.
* The chain is **linear**: each event's parent is the previous event's id, the first has the empty parent. lex-trail allows a tree;
  a log has one head. **Open:** a tree of parents needs an id index (design section 8's first bullet) and is not built.

## 2. What the chain proves, and what it does not

lex-trail's limit, inherited and not improved on. Each row is **measured** by `tests/trail_sweep.py` (below):

| attack on the file | what a verify says |
|---|---|
| a payload edited in place, checksum made right again | refused: the event's id no longer matches (`why 3`), at that record |
| the same edit with the event's id recomputed | that event passes, the *next* one is refused: its parent is the old id (`why 4`) |
| a record deleted, or two swapped | refused: the parent link breaks (`why 4`) |
| the first event deleted | refused: the new first event names a parent (`why 4`) |
| a record id the event does not determine | refused (`why 5`) |
| any single flipped bit (sampled over two records) | never clean, and the walk stops at the damaged record |
| the tail cut at a record boundary | **verifies clean.** Invisible from inside the log |
| the whole chain rewritten consistently | **verifies clean.** Invisible from inside the log |

The last two close only against an **anchor** held where the log's owner cannot reach it: `verify <dir> <count> <id>` requires the
event number `count` to have that id. A chain cut below `count` answers `anchor 2` (short), a rewritten one `anchor 3` (mismatch),
and both exit 3; a chain that holds the anchor answers `anchor 1`. **This engine adds durability to a trail and adds nothing to what
the trail proves.** A signed checkpoint is the caller's to make and keep; the anchor here is `(count, head id)`, which is what a
linear chain needs, and the set-of-ids digest of `SPEC.md` section 3 is `trail.anchor_digest` for callers that also speak that format.

## 3. Appending

`trail.append` takes the parent **explicitly**. That is what makes a retry safe: if an append's answer is lost, the caller sends the
same event again, and when that event is already the head the answer is `1` (already there) and nothing is written. Any other event
on a parent that is no longer the head is `6` (not the head). A caller therefore cannot create a fork, and cannot write one event
twice by retrying. **Measured** in `tests/trail_sweep.py` (`refusals`): the retry, the stale retry after the head moved on, and every
refusal leave the file byte for byte unchanged.

| status | meaning |
|---|---|
| 0 | written (durable only after `log.flush`) |
| 1 | already the head: a retry, nothing written |
| 2, 3, 4, 5 | the event is not storable: kind, parent, payload, time |
| 6 | the parent is not the head |
| 7 | longer than the log's limit or the caller's buffer |
| 8 | the log is broken (a failed write or flush; reopen and recover) |
| 9 | a write failed; the second answer is the errno, and the log is broken now |

A failed write is **measured** against `/dev/full`: status 9, errno 28, exit 3.

## 4. Verifying

`trail.verify` walks a file once with `record.check` and then checks each event: the five names, each field, the recomputed id, the
parent, the derived record id. It answers `(verdict, valid_end, events, why, anchor)`: verdicts 0 clean, 1 torn tail, 2 damaged
record, 3 unreadable, 4 broken chain (every record whole, an event wrong). `valid_end` is the end of the last good event.
`trailtool append` verifies before it appends and **refuses to extend a chain that is not good** (it writes nothing); a torn tail is
cut first, as `log.recover` does for any log.

The model in `tests/trail_sweep.py` is written independently (hashlib, its own CRC-32C and layout): **measured**, forty random chains
with equal, forward and backward times, empty and non-ASCII payloads verify clean with the model's head, and the tool's own appends
produce, byte for byte, the file the model writes. A cut at every one of a chain's byte offsets gives the model's verdict and `valid_end`.

## 5. Not done

* **Queries** (by id, by time range, by `task_id` in the payload). They need the id index and the JSON tape parser; a linear chain
  does not need them to be checked.
* **Signed checkpoints.** The caller's: the engine would need a key, and design section 8 is explicit that a checkpoint held by the log's
  owner is worth nothing. cancho-chain signs its own with secp256k1.
* **Rolling segments.** Trail mode is one segment (`0.seg`) until L0's rolling lands; `verify` takes a whole file.
* **Throughput.** A SHA-256 per append and per verified event, in software; not measured. **Open.**

## 6. The gate

    cancho fmt --check src tests bench
    cancho test tests/trail_test.cho src/trail.cho src/record.cho src/crc.cho src/log.cho src/segment.cho --std       # 15 passed
    cancho test tests/trail_vectors_test.cho src/trail.cho src/record.cho src/crc.cho src/log.cho src/segment.cho --std # 10 passed
    cancho build src/trailtool.cho src/trail.cho src/log.cho src/segment.cho src/record.cho src/crc.cho --std -o build/trailtool
    python3 tests/trail_sweep.py build/trailtool                                                                          # 167 checks
    CANCHO=... python3 tests/trail_mutants.py                                                                             # 85 mutants: 82 killed, 3 explained

`tests/trail_mutants.py` breaks `src/trail.cho` one fault at a time and requires a test to notice. **Measured:** 85 faults (every
boundary of the hex, digit and length checks; each separator and field of the id; each of the five names; the derivation; the anchor and
its sort; the parent and retry rules; the head kept across an append): **82 killed, 3 survive, each explained in the script and none
a missing test of reachable behaviour**:

* `verify: window size` -- a window of exactly `max_len` to `max_len + 3` bytes cannot be built from `trailtool`; `segment.scan` carries
  the same guard and `tests/sweep.py` covers it.
* `append: broken log` and `append: too long (log)` -- `log.append` makes the same two checks and `trail` maps its answers to the same
  codes; the early checks only decide which code wins when two things are wrong at once.

The first run killed 64 of 85; the 21 survivors were tests missing (a character just outside each hex range in the first and last
place, a prefix sorting first, a sixth pair, each name wrong on its own, a 19-digit time that overflows, the walk's stop offset, the
head the tool prints after an append, a record too long for the buffer, and a failed write), and each became a test.
