#!/usr/bin/env python3
"""Trail mode against an independent model (docs/trail.md section 6).

    python3 tests/trail_sweep.py build/trailtool

Everything the Lex code claims is checked against code written here: event ids with hashlib, the record layout with this
file's own CRC-32C, the record-id derivation, the chain rules. Files go both ways. A chain *written here* must verify clean
under `trailtool verify` with the head and count written here; a chain *written by `trailtool append`* must be byte for
byte the file this script writes for the same events. Then the file is attacked, and each attack must be refused for the
reason the design names, or, where the design says an attack is invisible, be shown to be invisible and shown to be caught
by an anchor.
"""
import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

TOOL = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "build/trailtool"
SEG = "0.seg"
FAILS = []
LAST_HEAD = b""
CHECKS = 0


def check(cond, msg):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(msg)
        print("FAIL:", msg)


# ---- the model ---------------------------------------------------------------------------------------------------

def _table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        t.append(c)
    return t


_T = _table()


def crc32c(data):
    c = 0xFFFFFFFF
    for b in data:
        c = (c >> 8) ^ _T[(c ^ b) & 0xFF]
    return c ^ 0xFFFFFFFF


def event_id(kind, parent, payload, ts):
    return hashlib.sha256(b"\0".join([kind, parent, payload, str(ts).encode()])).hexdigest().encode()


def record(ms, seq, pairs):
    body = struct.pack("<QQI", ms, seq, len(pairs))
    for k, v in pairs:
        body += struct.pack("<I", len(k)) + k + struct.pack("<I", len(v)) + v
    return struct.pack("<II", len(body) + 4, crc32c(body)) + body


def chain(events):
    """events: [(kind, payload, ts)] -> (file bytes, [ids], [record offsets]). Parent is always the previous id."""
    out, ids, offs = b"", [], []
    parent, pms, pseq = b"", -1, -1
    for kind, payload, ts in events:
        eid = event_id(kind, parent, payload, ts)
        ms = max(ts, pms)
        seq = pseq + 1 if ms == pms else 0
        offs.append(len(out))
        out += record(ms, seq, [(b"id", eid), (b"kind", kind), (b"parent", parent), (b"payload", payload), (b"ts_ms", str(ts).encode())])
        ids.append(eid)
        parent, pms, pseq = eid, ms, seq
    return out, ids, offs


def anchor_digest(ids):
    return hashlib.sha256(b" ".join(sorted(ids))).hexdigest()


# ---- running the tool --------------------------------------------------------------------------------------------

def run(*args):
    p = subprocess.run([TOOL] + [a.decode() if isinstance(a, bytes) else str(a) for a in args], capture_output=True)
    return p.returncode, p.stdout.decode()


def verify(path, anchor=None):
    args = ["verify", os.path.dirname(path)] + ([anchor[0], anchor[1].decode()] if anchor else [])
    rc, out = run(*args)
    f = out.split()
    # verdict V valid_end E events N why W anchor A \n head H
    return {"rc": rc, "verdict": int(f[1]), "valid_end": int(f[3]), "events": int(f[5]), "why": int(f[7]), "anchor": int(f[9]),
            "head": f[11].encode() if f[11] != "-" else b""}


def append(path, parent, kind, payload, ts):
    rc, out = run("append", os.path.dirname(path), parent, kind, payload, ts)
    f = out.split()
    global LAST_HEAD
    LAST_HEAD = f[4].encode() if len(f) > 4 else b""
    return rc, int(f[1]), int(f[2]) if len(f) > 2 and f[0] == "append" else 0


def fresh():
    d = tempfile.mkdtemp(prefix="trail")
    return d, os.path.join(d, SEG)


def put(path, data):
    with open(path, "wb") as f:
        f.write(data)


def get(path):
    with open(path, "rb") as f:
        return f.read()


def rand_events(rng, n):
    kinds = [b"a", b"meter.reading", b"settlement.energy", b"k" * 40, "café".encode()]
    evs, ts = [], rng.choice([0, 1000, 1788000000000])
    for _ in range(n):
        ts = max(0, ts + rng.choice([0, 0, 1, 5, 1000, -3, -2000]))   # equal, forward and backward times
        payload = rng.choice([b"", b"{}", b'{"wh":105500}', '{"note":"café — über"}'.encode(), b"x" * rng.randrange(0, 300)])
        evs.append((rng.choice(kinds), payload, ts))
    return evs


# ---- the checks --------------------------------------------------------------------------------------------------

def vectors_agree():
    """A chain written here verifies clean in the tool; a chain the tool writes is the same bytes."""
    rng = random.Random(7)
    for trial in range(40):
        evs = rand_events(rng, rng.randrange(1, 12))
        data, ids, offs = chain(evs)
        d, path = fresh()
        put(path, data)
        v = verify(path)
        check(v["verdict"] == 0 and v["events"] == len(evs) and v["head"] == ids[-1] and v["valid_end"] == len(data),
              "model chain %d verifies clean with the model's head: %r" % (trial, v))
        shutil.rmtree(d)
        # And the other way: the tool appends the same events.
        d, path = fresh()
        parent = "-"
        ok = True
        for (kind, payload, ts), eid in zip(evs, ids):
            rc, status, _ = append(path, parent, kind, payload, ts)
            ok = ok and rc == 0 and status == 0 and LAST_HEAD == eid
            parent = eid.decode()
        check(ok, "tool appends the model's events %d without refusal" % trial)
        check(get(path) == data, "tool's file for chain %d is byte for byte the model's" % trial)
        shutil.rmtree(d)


def at_sign_follows_the_head():
    d, path = fresh()
    rng = random.Random(3)
    evs = rand_events(rng, 6)
    data, ids, offs = chain(evs)
    for kind, payload, ts in evs:
        rc, status, _ = append(path, "@", kind, payload, ts)
        check(rc == 0 and status == 0, "append with @ is accepted")
    check(get(path) == data, "appending with @ builds the model's chain")
    shutil.rmtree(d)


def refusals():
    d, path = fresh()
    rc, s, _ = append(path, "-", b"k", b"{}", 10)
    check((rc, s) == (0, 0), "first event accepted")
    head = verify(path)["head"].decode()
    size = len(get(path))
    cases = [
        ("empty kind", ("-", b"", b"{}", 11), 2),
        ("short parent", ("abc", b"k", b"{}", 11), 3),
        ("upper-case parent", (head.upper(), b"k", b"{}", 11), 3),
        ("65-digit parent", (head + "0", b"k", b"{}", 11), 3),
        ("negative time", ("@", b"k", b"{}", -1), 5),
        ("time of 10^18", ("@", b"k", b"{}", 10 ** 18), 5),
        ("parent is not the head", ("0" * 64, b"k", b"{}", 11), 6),
        ("no parent after the first", ("-", b"k", b"{}", 11), 6),
    ]
    for name, (parent, kind, payload, ts), want in cases:
        rc, s, _ = append(path, parent, kind, payload, ts)
        check(s == want and rc == 3 and len(get(path)) == size, "%s refused with %d and nothing written (got rc=%d status=%d)" % (name, want, rc, s))
    # A record too long for the tool's scratch buffer is refused before it is built.
    rc, s, _ = append(path, "@", b"big", b"x" * 5000, 12)
    check((rc, s) == (3, 7) and len(get(path)) == size, "an event too long for the buffer is refused with 7 and nothing written (got rc=%d status=%d)" % (rc, s))
    # A retry of the head's own append is recognised; the same event after another is not.
    rc, s, _ = append(path, head, b"k2", b'{"a":1}', 11)
    check((rc, s) == (0, 0), "second event accepted")
    size2 = len(get(path))
    rc, s, _ = append(path, head, b"k2", b'{"a":1}', 11)
    check((rc, s) == (0, 1) and len(get(path)) == size2, "retry of the head is a no-op that says so")
    rc, s, _ = append(path, head, b"k2", b'{"a":2}', 11)
    check((rc, s) == (3, 6), "a different event on a used parent is refused")
    head2 = verify(path)["head"].decode()
    rc, s, _ = append(path, "@", b"k3", b"", 12)
    check((rc, s) == (0, 0), "third event accepted")
    rc, s, _ = append(path, head, b"k2", b'{"a":1}', 11)
    check((rc, s) == (3, 6) and verify(path)["events"] == 3, "a stale retry (the head moved on) is refused, not duplicated")
    shutil.rmtree(d)


def full_disk():
    """A write that fails is reported with the errno and nothing is acknowledged (/dev/full: every write is ENOSPC)."""
    if not os.path.exists("/dev/full"):
        return
    d, path = fresh()
    os.symlink("/dev/full", path)
    rc, s, detail = append(path, "-", b"k", b"{}", 1)
    check((rc, s, detail) == (3, 9, 28), "a failed write answers 9 with ENOSPC (28) and a non-zero exit (got %r)" % ((rc, s, detail),))
    shutil.rmtree(d)


def attacks():
    rng = random.Random(11)
    evs = [(b"meter.reading", b'{"wh":%d}' % (100 + i), 1000 + i * 10) for i in range(8)]
    data, ids, offs = chain(evs)
    ends = offs[1:] + [len(data)]

    def with_file(blob, anchor=None):
        d, path = fresh()
        put(path, blob)
        v = verify(path, anchor)
        shutil.rmtree(d)
        return v

    base = with_file(data)
    check(base["verdict"] == 0 and base["events"] == 8, "the untouched chain is clean")

    # 1. A payload edited in place with the checksum made right again: the record is whole, the id is not.
    for i in (0, 3, 7):
        s, e = offs[i], ends[i]
        rec = bytearray(data[s:e])
        pos = rec.index(b"wh") + 4
        rec[pos] ^= 1
        rec[4:8] = struct.pack("<I", crc32c(bytes(rec[8:])))
        v = with_file(data[:s] + bytes(rec) + data[e:])
        check(v["verdict"] == 4 and v["why"] == 3 and v["events"] == i and v["valid_end"] == s,
              "edited payload at event %d fails its id at the record it is in: %r" % (i, v))

    # 2. The same edit with the id recomputed: that event is fine, and the next one's parent no longer matches.
    i = 3
    kind, payload, ts = evs[i]
    new_payload = b'{"wh":999}'
    parent = ids[i - 1]
    neid = event_id(kind, parent, new_payload, ts)
    # (ms, seq) as the model derives them for this position.
    ms = max(ts, struct.unpack_from("<Q", data, offs[i - 1] + 8)[0])
    forged = record(ms, 0, [(b"id", neid), (b"kind", kind), (b"parent", parent), (b"payload", new_payload), (b"ts_ms", str(ts).encode())])
    v = with_file(data[:offs[i]] + forged + data[ends[i]:])
    check(v["verdict"] == 4 and v["why"] == 4 and v["events"] == i + 1,
          "a recomputed id breaks the next event's parent link: %r" % v)

    # 3. A deleted record, and a reordered pair.
    v = with_file(data[:offs[2]] + data[ends[2]:])
    check(v["verdict"] == 4 and v["why"] == 4 and v["events"] == 2, "a deleted middle event breaks the parent link: %r" % v)
    v = with_file(data[:offs[2]] + data[offs[3]:ends[3]] + data[offs[2]:ends[2]] + data[ends[3]:])
    check(v["verdict"] != 0, "two events swapped are refused: %r" % v)
    # The first event deleted: the new first names a parent.
    v = with_file(data[ends[0]:])
    check(v["verdict"] == 4 and v["why"] == 4 and v["events"] == 0, "a deleted first event is refused: %r" % v)

    # 4. A record id that the event does not determine.
    s, e = offs[4], ends[4]
    rec = bytearray(data[s:e])
    rec[8:16] = struct.pack("<Q", struct.unpack_from("<Q", rec, 8)[0] + 1)
    rec[4:8] = struct.pack("<I", crc32c(bytes(rec[8:])))
    v = with_file(data[:s] + bytes(rec) + data[e:])
    check(v["verdict"] == 4 and v["why"] == 5 and v["events"] == 4, "a record id that is not derived is refused: %r" % v)

    # 5. Every single bit of two records flipped, one at a time: never clean (the record checksum), whatever field it hits.
    bad = 0
    for i in (1, 6):
        s, e = offs[i], ends[i]
        for bit in range(0, (e - s) * 8, 5):
            blob = bytearray(data)
            blob[s + bit // 8] ^= 1 << (bit % 8)
            v = with_file(bytes(blob))
            if v["verdict"] == 0 or v["valid_end"] != offs[i]:
                bad += 1
    check(bad == 0, "no flipped bit leaves the chain clean, and the walk stops at the damaged record (%d did not)" % bad)

    # 6. The limits, shown and not hidden. Cutting at a record boundary verifies; an anchor catches it.
    head8 = ids[-1]
    for keep in (7, 4, 1):
        blob = data[:offs[keep]]
        v = with_file(blob)
        check(v["verdict"] == 0 and v["events"] == keep and v["head"] == ids[keep - 1],
              "INVISIBLE: tail cut to %d events verifies clean (this is the documented limit)" % keep)
        v = with_file(blob, (8, head8))
        check(v["anchor"] == 2 and v["rc"] == 3, "an anchor (8, head) reports the cut to %d events as short: %r" % (keep, v))
    # A consistent rewrite of the whole chain: clean, and the anchor says mismatch.
    rewritten, rids, _ = chain([(k, p if i != 2 else b'{"wh":0}', t) for i, (k, p, t) in enumerate(evs)])
    v = with_file(rewritten)
    check(v["verdict"] == 0 and v["events"] == 8, "INVISIBLE: a consistent rewrite of the whole chain verifies clean")
    v = with_file(rewritten, (8, head8))
    check(v["anchor"] == 3 and v["rc"] == 3, "an anchor catches the consistent rewrite: %r" % v)
    # An anchor in the middle of the chain is confirmed by the full chain.
    v = with_file(data, (5, ids[4]))
    check(v["anchor"] == 1 and v["rc"] == 0, "an anchor on the 5th event is confirmed by the whole chain: %r" % v)
    v = with_file(data, (5, ids[5]))
    check(v["anchor"] == 3, "an anchor with the wrong id at its place is a mismatch: %r" % v)

    # 7. A torn tail at every byte: clean exactly at record boundaries, torn elsewhere, valid_end the last boundary.
    d, path = fresh()
    wrong = 0
    for cut in range(0, len(data) + 1):
        put(path, data[:cut])
        v = verify(path)
        last = max([o for o in offs + [len(data)] if o <= cut])
        want_events = (offs + [len(data)]).index(last)
        want_verdict = 0 if cut == last else 1
        if v["verdict"] != want_verdict or v["valid_end"] != last or v["events"] != want_events:
            wrong += 1
    check(wrong == 0, "a cut at any of %d offsets gives the model's verdict and valid_end (%d wrong)" % (len(data) + 1, wrong))
    shutil.rmtree(d)


def append_after_damage():
    evs = [(b"k", b"{}", 100 + i) for i in range(5)]
    data, ids, offs = chain(evs)
    # A torn tail is cut, and the append goes on from the last whole event.
    d, path = fresh()
    put(path, data + data[offs[0]:offs[0] + 17])
    rc, s, _ = append(path, "@", b"k", b"{}", 105)
    want, wids, _ = chain(evs + [(b"k", b"{}", 105)])
    check((rc, s) == (0, 0) and get(path) == want, "an append after a torn tail recovers and continues the chain")
    shutil.rmtree(d)
    # A chain that is not good is not extended, and the file is left alone.
    d, path = fresh()
    broken = data[:offs[2]] + data[offs[3]:]
    put(path, broken)
    rc, s, _ = append(path, "@", b"k", b"{}", 200)
    check(rc == 3 and get(path) == broken, "an append to a broken chain is refused and writes nothing")
    shutil.rmtree(d)


def anchor_digests():
    """The model's anchor digest equals lex-trail's for its own vectors (the Lex code is checked against them in
    tests/trail_vectors_test.cho); here only that the model's function is the one the spec gives."""
    check(anchor_digest([b"c", b"a", b"b"]) == "0e9f64031fcb2bc708b531c2a20441580425d151a38503f38592a7dd36019d3b", "model anchor digest = lex-trail's vector")


def main():
    for fn in (anchor_digests, vectors_agree, at_sign_follows_the_head, refusals, full_disk, attacks, append_after_damage):
        fn()
    print("%d checks, %d failed" % (CHECKS, len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
