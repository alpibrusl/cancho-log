edition 5;

module segment;

import record;

// `segment` -- one segment file read back, and cut to its last whole record (`docs/design.md` sections 3 and 6).
//
// `scan` walks a file from the front with `record.check` and stops at the first thing that is not a whole record,
// saying where, why, and what it saw before. What is done about the stopping place is the caller's: the **active**
// segment is cut to it (a torn tail is the one thing a crash can leave there), and a **sealed** one that does not
// scan clean is refused (damage below the durable point is corruption, not a crash artefact, and cutting it would
// turn bit rot into silent loss).
//
// Two things beyond `record.check` are enforced here, because a single record cannot know them: ids must strictly
// increase within a segment (a record that parses but goes backwards is `bad`), and the buffer must be able to hold
// the largest record the log allows.

// `scan`'s verdicts.
pub fn clean() -> [] int {
    return 0;
}

// The file ends in the middle of a record: the shape of a torn write.
pub fn torn() -> [] int {
    return 1;
}

// A record that is not one, or whose id does not increase.
pub fn damaged() -> [] int {
    return 2;
}

// The file could not be read, or the buffer is too small for `max_len`.
pub fn unreadable() -> [] int {
    return 3;
}

// Does `(ms, seq)` come strictly after `(prev_ms, prev_seq)`?
fn after(ms: int, seq: int, prev_ms: int, prev_seq: int) -> [] bool {
    if ms != prev_ms {
        return ms > prev_ms;
    }
    return seq > prev_seq;
}

// Scan `file`, whose length is `size`, from its first byte. `buf` is the window records are read through, and must be
// at least `max_len + 4` bytes. `prev_ms` and `prev_seq` are the id the first record must exceed (use -1 and -1 for
// the first segment of a stream).
//
// Answers `(verdict, valid_end, records, last_ms, last_seq)`: how the scan ended, the offset of the end of the last
// whole record (so everything past it is what to cut), how many whole records there were, and the last one's id (the
// `prev` values, unchanged, if there were none).
pub fn scan[&f, &b](file: &!f File, size: int, buf: &!b [byte], max_len: int, prev_ms: int, prev_seq: int) -> [file_read] (int, int, int, int, int) {
    if len(buf) < max_len + 4 {
        return (unreadable(), 0, 0, prev_ms, prev_seq);
    }
    var pos = 0;
    var records = 0;
    var last_ms = prev_ms;
    var last_seq = prev_seq;
    while pos < size {
        var want = size - pos;
        if want > len(buf) {
            want = len(buf);
        }
        // One read per window. A short read is not an error: whatever came back is scanned, and a record that runs
        // past it is read again from its own start.
        var got = 0;
        match file_pread(file, pos, buf[0..want]) {
            Read::Got(n) => {
                got = n;
            }
            Read::End => {
                return (torn(), pos, records, last_ms, last_seq);
            }
            Read::Failed(e) => {
                return (unreadable(), pos, records, last_ms, last_seq);
            }
        }
        var at = 0;
        var refill = false;
        while at < got && !refill {
            let r = record.check(buf, at, got, max_len);
            if r.0 == record.ok() {
                let ms = record.ms_of(buf, at);
                let seq = record.seq_of(buf, at);
                if !after(ms, seq, last_ms, last_seq) {
                    return (damaged(), pos + at, records, last_ms, last_seq);
                }
                last_ms = ms;
                last_seq = seq;
                records = records + 1;
                at = at + r.1;
            } else if r.0 == record.bad() {
                return (damaged(), pos + at, records, last_ms, last_seq);
            } else if pos + got < size {
                // The record may continue past this window: read again from where it starts.
                refill = true;
            } else {
                return (torn(), pos + at, records, last_ms, last_seq);
            }
        }
        pos = pos + at;
        if at == 0 && !refill {
            // Nothing consumed and nothing asked for again: cannot happen, and must not loop.
            return (unreadable(), pos, records, last_ms, last_seq);
        }
        if at == 0 {
            // A refill that made no progress means the record is longer than the window.
            return (unreadable(), pos, records, last_ms, last_seq);
        }
    }
    return (clean(), pos, records, last_ms, last_seq);
}
