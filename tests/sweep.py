#!/usr/bin/env python3
"""The crash sweep (docs/design.md section 6.2): cut and corrupt real files at every offset and require recovery to
yield a prefix that contains every acknowledged record.

    python3 tests/sweep.py build/logtool

`logtool write` produces a real segment and says which records it wrote and when it synced; this script then builds the
files a crash could leave (a cut at every byte past the last sync, a block of the unsynced region zeroed or replaced
by other bytes, bits flipped in a sealed segment), runs `logtool recover` on each, and checks the answer against an
independent reader of the format written here in Python, with its own CRC-32C. Nothing in this file imports or shares
code with the Lex implementation: if they agree, it is not because they are the same code.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

TOOL = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "build/logtool"
MAX_LEN = 65536  # logtool's constant
SEG = "0.seg"


# ---- an independent reader of the record format (design section 4) ----------------------------------------------

def _crc_table():
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        table.append(c)
    return table


_TABLE = _crc_table()


def crc32c(data):
    c = 0xFFFFFFFF
    for b in data:
        c = (c >> 8) ^ _TABLE[(c ^ b) & 0xFF]
    return c ^ 0xFFFFFFFF


assert crc32c(b"123456789") == 0xE3069283


def encode(ms, seq, pairs):
    """A record, written here and not by the Lex code."""
    body = struct.pack("<QQI", ms, seq, len(pairs))
    for key, value in pairs:
        body += struct.pack("<I", len(key)) + key + struct.pack("<I", len(value)) + value
    return struct.pack("<I", len(body) + 4) + struct.pack("<I", crc32c(body)) + body


def parse(data):
    """The longest valid prefix of `data`: ([(ms, seq, end_offset)], valid_end, verdict) with verdict 0 clean, 1 torn,
    2 damaged, exactly as logtool reports them."""
    at, records, prev = 0, [], (-1, -1)
    while at < len(data):
        if len(data) - at < 4:
            return records, at, 1
        (length,) = struct.unpack_from("<I", data, at)
        if length < 24 or length > MAX_LEN:
            return records, at, 2
        total = 4 + length
        if len(data) - at < total:
            return records, at, 1
        (stored,) = struct.unpack_from("<I", data, at + 4)
        if stored != crc32c(data[at + 8 : at + total]):
            return records, at, 2
        ms, seq, fields = struct.unpack_from("<QQI", data, at + 8)
        if ms >= 1 << 63 or seq >= 1 << 63:
            return records, at, 2
        end, p, ok = at + total, at + 28, True
        for _ in range(fields):
            if end - p < 4:
                ok = False
                break
            (kl,) = struct.unpack_from("<I", data, p)
            p += 4
            if end - p < kl + 4:
                ok = False
                break
            p += kl
            (vl,) = struct.unpack_from("<I", data, p)
            p += 4
            if end - p < vl:
                ok = False
                break
            p += vl
        if not ok or p != end:
            return records, at, 2
        if (ms, seq) <= prev:
            return records, at, 2
        prev = (ms, seq)
        records.append((ms, seq, at + total))
        at += total
    return records, at, 0


# ---- running the tool ----------------------------------------------------------------------------------------------

def run(*args):
    return subprocess.run([TOOL, *args], capture_output=True, text=True)


def write_log(directory, count, sync_every, seed):
    out = run("write", directory, str(count), str(sync_every), str(seed))
    assert out.returncode == 0, out.stderr
    ends, syncs = [], []
    for line in out.stdout.splitlines():
        w = line.split()
        if w[0] == "rec":
            ends.append(int(w[2]))
        elif w[0] == "sync":
            syncs.append(int(w[1]))
    return ends, syncs


def recover(directory, mode):
    out = run("recover", directory, mode)
    fields = {}
    for line in out.stdout.splitlines():
        w = line.split()
        if w and w[0] == "verdict":
            fields = dict(zip(w[0::2], map(int, w[1::2])))
    return out.returncode, fields


class Sandbox:
    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="sweep-")
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)

    def image(self, data):
        d = tempfile.mkdtemp(dir=self.root)
        with open(os.path.join(d, SEG), "wb") as f:
            f.write(data)
        return d


failures = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        failures.append(message)
        if len(failures) <= 20:
            print("FAIL:", message)


def check_active(sb, image, acked_end, label):
    """Recovery of `image` as an active segment: it must find exactly the longest valid prefix (the oracle's), keep every
    record that ended at or before `acked_end`, cut the file to exactly that prefix, and be idempotent."""
    want, want_end, want_verdict = parse(image)
    d = sb.image(image)
    code, got = recover(d, "active")
    check(code == 0, f"{label}: recover exited {code}")
    check(got.get("verdict") == want_verdict, f"{label}: verdict {got.get('verdict')} != {want_verdict}")
    check(got.get("valid_end") == want_end, f"{label}: valid_end {got.get('valid_end')} != {want_end}")
    check(got.get("records") == len(want), f"{label}: records {got.get('records')} != {len(want)}")
    kept = sum(1 for _, _, e in want if e <= acked_end)
    acked = sum(1 for e in ACKED_ENDS if e <= acked_end)
    check(kept == acked, f"{label}: an acknowledged record is not in the valid prefix ({kept} of {acked})")
    after = open(os.path.join(d, SEG), "rb").read()
    check(after == image[:want_end], f"{label}: file after recovery is not exactly the valid prefix")
    code2, again = recover(d, "active")
    check(code2 == 0 and again.get("verdict") == 0, f"{label}: a second recovery was not clean")
    check(open(os.path.join(d, SEG), "rb").read() == after, f"{label}: a second recovery changed the file")


def main():
    global ACKED_ENDS
    with Sandbox() as sb:
        # ---- one real log -------------------------------------------------------------------------------------
        base = os.path.join(sb.root, "base")
        os.makedirs(base)
        count, sync_every = 24, 5
        ends, syncs = write_log(base, count, sync_every, 3)
        image = open(os.path.join(base, SEG), "rb").read()
        check(len(ends) == count, "writer wrote every record")
        check(ends[-1] == len(image), "the writer's last offset is the file's size")
        oracle, oracle_end, oracle_verdict = parse(image)
        check(oracle_verdict == 0 and len(oracle) == count, "the independent reader parses the whole file")
        check([e for _, _, e in oracle] == ends, "the independent reader and the writer agree on every record's end")
        acked_end = syncs[-1]  # the last flush: everything below it survives any crash
        ACKED_ENDS = [e for e in ends if e <= acked_end]
        ends_of_base = list(ACKED_ENDS)
        check(len(ACKED_ENDS) < count, "the log has an unsynced tail to sweep")
        n = len(image)
        print(f"log: {count} records, {n} bytes, last sync at {acked_end}, {len(ACKED_ENDS)} acknowledged")

        # ---- 1. the clean file, as active and as sealed ---------------------------------------------------------
        d = sb.image(image)
        code, got = recover(d, "sealed")
        check(code == 0 and got.get("verdict") == 0 and got.get("records") == count, "a whole sealed segment opens")

        # ---- 2. a cut at every byte from the last sync to the end -----------------------------------------------
        for k in range(acked_end, n + 1):
            check_active(sb, image[:k], acked_end, f"cut at {k}")

        # ---- 3. a block of the unsynced region zeroed, or replaced by bytes from elsewhere ------------------------
        for block in (64, 512):
            first = (acked_end // block) * block
            for start in range(first, n, block):
                lo, hi = max(start, acked_end), min(start + block, n)
                if lo >= hi:
                    continue
                zeroed = image[:lo] + bytes(hi - lo) + image[hi:]
                check_active(sb, zeroed, acked_end, f"zeroed {lo}:{hi}")
                donor = image[(lo + 97) % max(1, n - (hi - lo)) :][: hi - lo]
                donor = donor.ljust(hi - lo, b"\xa5")
                swapped = image[:lo] + donor + image[hi:]
                check_active(sb, swapped, acked_end, f"swapped {lo}:{hi}")

        # ---- 3b. records that are well formed and checksummed but whose ids do not increase ----------------------
        # A record can only know it is whole; whether it belongs is the segment's question. Each file here is valid byte
        # for byte, and recovery must stop at the first record whose id does not come after the one before it.
        for name, ids in (
            ("repeated id", [(1, 0), (2, 0), (3, 0), (3, 0), (4, 0)]),
            ("repeated ms, lower seq", [(1, 0), (2, 5), (2, 4), (3, 0)]),
            ("lower ms", [(5, 0), (6, 0), (4, 9), (7, 0)]),
            ("equal ms, higher seq is fine", [(1, 0), (1, 1), (1, 2)]),
            ("first record at id 0-0", [(0, 0), (0, 1)]),
        ):
            data = b"".join(encode(ms, seq, [(b"k", b"v%d" % i)]) for i, (ms, seq) in enumerate(ids))
            ends = []
            at = 0
            for i, (ms, seq) in enumerate(ids):
                at += len(encode(ms, seq, [(b"k", b"v%d" % i)]))
                ends.append(at)
            ACKED_ENDS = []
            check_active(sb, data, 0, f"ids: {name}")
            want, want_end, want_verdict = parse(data)
            if name.startswith("equal") or name.startswith("first"):
                check(want_verdict == 0 and len(want) == len(ids), f"ids: {name}: the oracle accepts it")
            else:
                check(want_verdict == 2 and len(want) < len(ids), f"ids: {name}: the oracle stops at the bad id")
        ACKED_ENDS = [e for e in ends_of_base]

        # ---- 3c. a log longer than the scan window (so records straddle a refill) ---------------------------------
        big = os.path.join(sb.root, "big")
        os.makedirs(big)
        big_count = 3500
        big_ends, big_syncs = write_log(big, big_count, 500, 11)
        big_image = open(os.path.join(big, SEG), "rb").read()
        window = 65536 + 4096
        check(len(big_image) > 2 * window, f"the big log spans more than two windows ({len(big_image)} bytes)")
        ACKED_ENDS = [e for e in big_ends if e <= big_syncs[-1]]
        check_active(sb, big_image, big_syncs[-1], "big: clean")
        # Cuts straddling every window-sized step, record by record, near each multiple of the window.
        cuts = set()
        for step in range(1, len(big_image) // window + 1):
            for e in big_ends:
                if abs(e - step * window) < 120:
                    cuts.update((e - 1, e, e + 1))
        cuts = sorted(c for c in cuts if big_syncs[-1] <= c <= len(big_image))
        for k in cuts:
            check_active(sb, big_image[:k], big_syncs[-1], f"big: cut at {k}")
        ACKED_ENDS = [e for e in ends_of_base]

        # ---- 3d. a truncation is flushed: the cut file must reach the disk before recovery says it is done -------
        if shutil.which("strace"):
            torn = image[: acked_end + 3] if acked_end + 3 < n else image
            d = sb.image(torn)
            trace = os.path.join(sb.root, "trace.txt")
            subprocess.run(["strace", "-f", "-e", "trace=ftruncate,fsync", "-o", trace, TOOL, "recover", d, "active"],
                           capture_output=True)
            lines = open(trace).read().splitlines()
            calls = [l.split("(")[0].split()[-1] for l in lines if "ftruncate(" in l or "fsync(" in l]
            check(calls == ["ftruncate", "fsync"], f"truncation is followed by exactly one fsync (saw {calls})")
            check(all(l.endswith("= 0") for l in lines if "ftruncate(" in l or "fsync(" in l), "and both succeed")
        else:
            print("strace is not installed; the flush of a truncation is not checked")

        # ---- 4. a bit flipped anywhere in a sealed segment must be refused ----------------------------------------
        for at in range(n):
            for bit in (0, 7):
                bad = bytearray(image)
                bad[at] ^= 1 << bit
                d = sb.image(bytes(bad))
                code, got = recover(d, "sealed")
                check(code == 3, f"sealed: a flip of bit {bit} at byte {at} was not refused (exit {code})")
                check(open(os.path.join(d, SEG), "rb").read() == bytes(bad), f"sealed: refusal at {at} changed the file")

    print(f"{checks} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
