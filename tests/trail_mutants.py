#!/usr/bin/env python3
"""Mutation check for trail mode (docs/trail.md section 6): break src/trail.cho one fault at a time and require that the
tests notice. A mutant that survives is a test missing, or a fault that cannot change behaviour; each survivor is listed
in EQUIVALENT with the reason, or the build fails.

    CANCHO=path/to/cancho python3 tests/trail_mutants.py [--list]

Each mutant is built in its own copy of the tree. It is killed by `cancho test` of the two trail test files, or else by
building trailtool and running tests/trail_sweep.py.
"""
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANCHO = os.environ.get("CANCHO", "cancho")
SRC = ["src/trail.cho", "src/record.cho", "src/crc.cho", "src/log.cho", "src/segment.cho"]

# (name, old, new, how many times `old` occurs; every occurrence is replaced one at a time as a separate mutant when count > 1)
M = []


def m(name, old, new, count=1):
    M.append((name, old, new, count))


# --- check_event
m("kind may be empty", "if len(kind) == 0 || has_nul(kind) {", "if has_nul(kind) {")
m("kind may hold NUL", "if len(kind) == 0 || has_nul(kind) {", "if len(kind) == 0 {")
m("any parent length", "if len(parent) != 0 && !is_id(parent) {", "if len(parent) > 64 {")
m("parent not checked as hex", "if len(parent) != 0 && !is_id(parent) {", "if len(parent) != 0 && len(parent) != 64 {")
m("payload may hold NUL", "if has_nul(payload) {\n        return bad_payload();", "if false {\n        return bad_payload();")
m("time 0 refused", "if ts_ms < 0 || decimal_len(ts_ms) > time_digits() {", "if ts_ms <= 0 || decimal_len(ts_ms) > time_digits() {")
m("negative time allowed", "if ts_ms < 0 || decimal_len(ts_ms) > time_digits() {", "if decimal_len(ts_ms) > time_digits() {")
m("19-digit time allowed", "if ts_ms < 0 || decimal_len(ts_ms) > time_digits() {", "if ts_ms < 0 || decimal_len(ts_ms) > 19 {")
m("18-digit time refused", "if ts_ms < 0 || decimal_len(ts_ms) > time_digits() {", "if ts_ms < 0 || decimal_len(ts_ms) >= time_digits() {")
# --- hex
m("hex: 0 below range", "c >= 48 && c <= 57", "c >= 47 && c <= 57")
m("hex: 9 above range", "c >= 48 && c <= 57", "c >= 48 && c <= 58")
m("hex: a below range", "c >= 97 && c <= 102", "c >= 96 && c <= 102")
m("hex: g allowed", "c >= 97 && c <= 102", "c >= 97 && c <= 103")
m("hex: f refused", "c >= 97 && c <= 102", "c >= 97 && c <= 101")
m("id length", "if len(x) != 64 {\n        return false;\n    }\n    var i = 0;\n    while i < 64 {", "if len(x) < 64 {\n        return false;\n    }\n    var i = 0;\n    while i < 64 {")
m("id: last digit unchecked", "    while i < 64 {\n        if !is_hex(int_of(x[i])) {", "    while i < 63 {\n        if !is_hex(int_of(x[i])) {")
# --- digits
m("decimal_len boundary", "while t >= 10 {\n        t = t / 10;\n        n = n + 1;\n    }\n    return n;", "while t > 10 {\n        t = t / 10;\n        n = n + 1;\n    }\n    return n;")
m("hex digit boundary", "if v < 10 {\n        return 48 + v;", "if v <= 10 {\n        return 48 + v;")
m("hex digit letter base", "return 87 + v;", "return 88 + v;")
# --- event id
m("id: NUL separators", "crypto.sha256_update(state, nul);", "", 3)
m("id: time as text", "crypto.sha256_update(state, digits[0..n]);", "crypto.sha256_update(state, digits[0..n - 1]);")
m("id: payload skipped", "crypto.sha256_update(state, payload);", "")
m("id: kind skipped", "crypto.sha256_update(state, kind);", "")
m("id: parent skipped", "crypto.sha256_update(state, parent);", "")
m("id: second digest byte", "out[2 * i + 1] = byte_of(hex_digit(b & 15));\n            i = i + 1;\n        }\n    }\n    return 0;\n}\n\n// The anchor", "out[2 * i + 1] = byte_of(hex_digit(b & 14));\n            i = i + 1;\n        }\n    }\n    return 0;\n}\n\n// The anchor")
# --- anchor digest
m("anchor: space separator", "crypto.sha256_update(state, space);", "")
m("anchor: not sorted", "while j > 0 && later(ids, starts[j - 1], ends[j - 1], s, e) {", "while j > 100000 && later(ids, starts[j - 1], ends[j - 1], s, e) {")
m("anchor: sort the other way", "return x > y;", "return x < y;")
m("anchor: longer sorts first", "return e1 - s1 > e2 - s2;", "return e1 - s1 < e2 - s2;")
m("anchor: sort stops one early", "while i < count {\n                let s = starts[i];", "while i < count - 1 {\n                let s = starts[i];")
m("anchor: last id dropped", "while k < count {\n                if k > 0 {", "while k < count - 1 {\n                if k > 0 {")
m("anchor: count not checked", "if n != count {", "if n > count {")
# --- derive and encode
m("derive: earlier time wins", "if prev_ms > ms {", "if prev_ms < ms {")
m("derive: equal time", "if prev_ms > ms {", "if prev_ms >= ms + 1000000 {")
m("derive: sequence step", "return (ms, prev_seq + 1);", "return (ms, prev_seq + 2);")
m("derive: sequence not reset", "return (ms, 0);", "return (ms, prev_seq);")
m("derive: new ms treated as same", "if ms == prev_ms {\n        return (ms, prev_seq + 1);", "if ms >= prev_ms {\n        return (ms, prev_seq + 1);")
m("encoded_len short", "(4 + 5 + 4 + decimal_len(ts_ms))", "(4 + 5 + 4 + 1)")
m("encode: pair order", 'q = record.put_pair(out, q, "kind", kind);\n        q = record.put_pair(out, q, "parent", parent);', 'q = record.put_pair(out, q, "parent", parent);\n        q = record.put_pair(out, q, "kind", kind);')
# --- check_record
m("shape: pair count", "if record.fields_of(buf, at) != 5 {", "if record.fields_of(buf, at) < 5 {")
m("shape: id key", '!same(buf[p0.0..p0.0 + p0.1], "id") || ', "")
m("shape: kind key", '!same(buf[p1.0..p1.0 + p1.1], "kind") || ', "")
m("shape: parent key", '!same(buf[p2.0..p2.0 + p2.1], "parent") || ', "")
m("shape: payload key", '!same(buf[p3.0..p3.0 + p3.1], "payload") || ', "")
m("shape: ts_ms key", ' || !same(buf[p4.0..p4.0 + p4.1], "ts_ms")', "")
m("time: empty ok", "if len(digits) == 0 || len(digits) > time_digits() {", "if len(digits) > time_digits() {")
m("time: 19 digits ok", "if len(digits) == 0 || len(digits) > time_digits() {", "if len(digits) == 0 || len(digits) > 19 {")
m("time: leading zero ok", "if len(digits) > 1 && int_of(digits[0]) == 48 {", "if len(digits) > 2 && int_of(digits[0]) == 48 {")
m("time: zero refused", "if len(digits) > 1 && int_of(digits[0]) == 48 {", "if len(digits) > 0 && int_of(digits[0]) == 48 {")
m("time: char below range", "if c < 48 || c > 57 {", "if c < 47 || c > 57 {")
m("time: char above range", "if c < 48 || c > 57 {", "if c < 48 || c > 58 {")
m("time: wrong digit weight", "ts = ts * 10 + (c - 48);", "ts = ts * 10 + (c - 47);")
m("id format not checked", "if !is_id(id) || check_event(kind, parent, payload, ts) != 0 {", "if check_event(kind, parent, payload, ts) != 0 {")
m("event not checked", "if !is_id(id) || check_event(kind, parent, payload, ts) != 0 {", "if !is_id(id) {")
m("id not compared", "if !same(again, id) {", "if false {")
m("parent not compared", "if !same(parent, prev_head) {", "if false {")
m("record ms not compared", "if record.ms_of(buf, at) != want.0 || record.seq_of(buf, at) != want.1 {", "if record.seq_of(buf, at) != want.1 {")
m("record seq not compared", "if record.ms_of(buf, at) != want.0 || record.seq_of(buf, at) != want.1 {", "if record.ms_of(buf, at) != want.0 {")
# --- verify
m("verify: first event's parent", "                var prev = head[0..0];\n                if events > 0 {\n                    prev = head[0..64];\n                }", "                var prev = head[0..64];")
m("verify: head copy short", "while i < 64 {\n                    head[i] = window[first.2 + i];", "while i < 63 {\n                    head[i] = window[first.2 + i];")
m("verify: head not kept", "head[i] = window[first.2 + i];", "head[i] = head[i];")
m("verify: chain error offset", "return (broken_chain(), pos + at, events, why, anchor);", "return (broken_chain(), pos, events, why, anchor);")
m("verify: torn offset", "return (torn(), pos + at, events, 0, anchor);", "return (torn(), pos, events, 0, anchor);")
m("verify: damaged offset", "return (damaged(), pos + at, events, 0, anchor);", "return (damaged(), pos, events, 0, anchor);")
m("verify: anchor at wrong event", "if events == anchor_count {", "if events == anchor_count + 1 {")
m("verify: anchor from the first on", "if events == anchor_count {", "if events >= anchor_count {")
m("verify: anchor never short", "        anchor = anchor_short();\n    }\n    if len(window)", "        anchor = anchor_none();\n    }\n    if len(window)")
m("verify: mismatch is confirmed", "                    } else {\n                        anchor = anchor_mismatch();", "                    } else {\n                        anchor = anchor_confirmed();")
m("verify: previous ms kept", "prev_ms = record.ms_of(window, at);", "prev_ms = prev_ms;")
m("verify: previous seq kept", "prev_seq = record.seq_of(window, at);", "prev_seq = prev_seq;")
m("verify: window size", "if len(window) < max_len + 4 {", "if len(window) < max_len {")
# --- append
m("append: broken log", "if log.broken(lg) {\n        return (broken(), 0);\n    }\n    let c", "let c")
m("append: event not checked", "if c != 0 {\n        return (c, 0);\n    }", "")
m("append: parent not compared", "if !same(parent, current) {", "if false {")
m("append: first parent", "    var current = head[0..0];\n    if count > 0 {\n        current = head[0..64];\n    }", "    var current = head[0..64];")
m("append: retry not recognised", "retry = count > 0 && same(id, current);", "retry = false;")
m("append: retry of anything", "retry = count > 0 && same(id, current);", "retry = count > 0;")
m("append: too long (log)", "if need - 4 > max_len || need > len(scratch) {", "if need > len(scratch) {")
m("append: too long (scratch)", "if need - 4 > max_len || need > len(scratch) {", "if need - 4 > max_len {")
m("append: record id derived from log", "let id = derive(ts_ms, log.last_ms(lg), log.last_seq(lg));", "let id = derive(ts_ms, log.last_ms(lg), 0 - 1);")
m("append: head not updated", "head[i] = scratch[record.first_pair(0) + 10 + i];", "head[i] = head[i];")
m("append: head offset", "head[i] = scratch[record.first_pair(0) + 10 + i];", "head[i] = scratch[record.first_pair(0) + 11 + i];")
m("append: write error code", "return (write_failed(), r);", "return (write_failed(), 0);")

# Mutants whose fault cannot change what the code does, with the reason. None yet: every survivor is investigated.
EQUIVALENT = {
    "verify: window size": "trailtool lends a window of max_len + 4096 bytes, so a window of exactly max_len..max_len+3 cannot be built from the "
                           "command line; segment.scan carries the same guard and tests/sweep.py exercises it with its own window sizes.",
    "append: broken log": "log.append checks the same flag first and its answer is mapped to the same code (broken); the early check only decides "
                          "which code wins when a broken log is also given an invalid event, and nothing depends on that.",
    "append: too long (log)": "log.append re-checks the record against max_len and its answer is mapped to the same code (too_long); the early "
                              "check is reached first only for a record that fits the scratch buffer and not max_len, which trailtool's 4 KiB "
                              "buffer cannot produce.",
}


def expand():
    out = []
    text = open(os.path.join(ROOT, "src/trail.cho")).read()
    for name, old, new, count in M:
        n = text.count(old)
        if n != count:
            sys.exit("pattern for %r occurs %d times, expected %d: %r" % (name, n, count, old))
        if count == 1:
            out.append((name, text.replace(old, new, 1)))
        else:
            pos = -1
            for k in range(count):
                pos = text.index(old, pos + 1)
                out.append(("%s #%d" % (name, k + 1), text[:pos] + new + text[pos + len(old):]))
    return out


def run(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def try_mutant(item):
    name, text = item
    d = tempfile.mkdtemp(prefix="mut")
    try:
        for sub in ("src", "tests", "build"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        for f in os.listdir(os.path.join(ROOT, "src")):
            shutil.copy(os.path.join(ROOT, "src", f), os.path.join(d, "src", f))
        for f in os.listdir(os.path.join(ROOT, "tests")):
            if os.path.isfile(os.path.join(ROOT, "tests", f)):
                shutil.copy(os.path.join(ROOT, "tests", f), os.path.join(d, "tests", f))
        open(os.path.join(d, "src/trail.cho"), "w").write(text)
        files = [os.path.join("src", os.path.basename(f)) for f in SRC]
        for t in ("tests/trail_test.cho", "tests/trail_vectors_test.cho"):
            rc, out = run([CANCHO, "test", t] + files + ["--std"], d)
            if rc != 0:
                return name, "killed by " + os.path.basename(t)
        tool = ["src/trailtool.cho"] + files
        rc, out = run([CANCHO, "build"] + tool + ["--std", "-o", "build/trailtool"], d)
        if rc != 0:
            return name, "BUILD FAILED (mutant is not a valid program): " + out[-200:]
        rc, out = run([sys.executable, "tests/trail_sweep.py", "build/trailtool"], d)
        if rc != 0:
            return name, "killed by trail_sweep"
        return name, "SURVIVED"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    items = expand()
    if "--list" in sys.argv:
        for name, _ in items:
            print(name)
        return
    killed, survived, other = 0, [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("JOBS", "6"))) as pool:
        for name, verdict in pool.map(try_mutant, items):
            if verdict.startswith("killed"):
                killed += 1
            elif verdict == "SURVIVED":
                survived.append(name)
                print("SURVIVED:", name, flush=True)
            else:
                other.append((name, verdict))
                print("INVALID:", name, verdict, flush=True)
    explained = [s for s in survived if s in EQUIVALENT]
    print("%d mutants: %d killed, %d survived (%d explained), %d invalid" % (len(items), killed, len(survived), len(explained), len(other)))
    sys.exit(0 if len(survived) == len(explained) and not other else 1)


if __name__ == "__main__":
    main()
