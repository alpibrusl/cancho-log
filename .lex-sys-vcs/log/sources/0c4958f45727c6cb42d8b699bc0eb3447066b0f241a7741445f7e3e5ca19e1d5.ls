edition 5;

module log;

import record;
import segment;

// `log` -- one stream's segment, open for appending and for reading (`docs/design.md` sections 3 to 6).
//
// The caller opens the files, so the caller's `Fs` decides what path this can reach and this module needs no capability
// of its own: three handles to the one file (`recover`'s read-write handle, then an append handle and a read handle that
// `attach` keeps). It holds what an append needs to know: where the file ends, how much of it a flush has covered, and the
// last id, which the next must exceed.
//
// **Append and flush are two steps, so that one flush can cover many appends** (group commit, design section 5). `append`
// writes and says nothing about durability; `flush` is the only thing that makes an append survive a crash, and a caller
// acknowledges a record only after the `flush` that covered it has answered 0.
//
// **A failed write or flush is not retried** (design section 5): after either, the file's contents are unknown, so the
// log marks itself broken and refuses everything until it is reopened and recovered.

pub res struct Log {
    writer: File,
    reader: File,
    // Where the file ends: the bytes written, flushed or not.
    size: int,
    // How much of the file the last successful flush covered. Everything below this survives a crash.
    synced: int,
    last_ms: int,
    last_seq: int,
    records: int,
    max_len: int,
    // Set by a failed write or flush; nothing else works after it.
    broken: bool,
}

// `append` answers 0 and these.
pub fn id_not_after() -> [] int {
    return 1;
}

pub fn too_long() -> [] int {
    return 2;
}

pub fn is_broken() -> [] int {
    return 3;
}

// Recover the file `rw` is open on: scan it and cut it to its last whole record, flushing the cut. `window` is the read buffer,
// at least `max_len + 4` bytes. Answers `(status, valid_end, records, last_ms, last_seq)`; `status` is 0, or an errno (a
// failed cut or flush) plus 1000 if the scan could not read the file.
pub fn recover[&f, &b](rw: &!f File, window: &!b [byte], max_len: int) -> [file_read, file_write] (int, int, int, int, int) {
    var size = 0;
    match file_size(rw) {
        Done::Ok(n) => {
            size = n;
        }
        Done::Failed(e) => {
            return (e, 0, 0, 0 - 1, 0 - 1);
        }
    }
    let r = segment.scan(rw, size, window, max_len, 0 - 1, 0 - 1);
    if r.0 == segment.unreadable() {
        return (1000, r.1, r.2, r.3, r.4);
    }
    if r.0 != segment.clean() {
        match file_truncate(rw, r.1) {
            Done::Ok(n) => {
                match file_sync(rw) {
                    Done::Ok(m) => {
                    }
                    Done::Failed(e) => {
                        return (e, r.1, r.2, r.3, r.4);
                    }
                }
            }
            Done::Failed(e) => {
                return (e, r.1, r.2, r.3, r.4);
            }
        }
    }
    return (0, r.1, r.2, r.3, r.4);
}

// Take over a recovered file: `writer` is open for append, `reader` for reading, and `valid_end`, `records` and the id are
// what `recover` answered. Everything up to `valid_end` was flushed by `recover` or by the process that wrote it.
pub fn attach(writer: File, reader: File, valid_end: int, records: int, last_ms: int, last_seq: int, max_len: int) -> [] Log {
    return Log { writer: writer, reader: reader, size: valid_end, synced: valid_end, last_ms: last_ms, last_seq: last_seq, records: records, max_len: max_len, broken: false };
}

// End the log, closing both handles. Answers 0, or the last close's error.
pub fn close(lg: Log) -> [] int {
    let Log { writer, reader, size, synced, last_ms, last_seq, records, max_len, broken } = lg;
    let a = file_close(writer);
    let b = file_close(reader);
    if a != 0 {
        return a;
    }
    return b;
}

pub fn size[&l](lg: &l Log) -> [] int {
    return lg.size;
}

pub fn synced[&l](lg: &l Log) -> [] int {
    return lg.synced;
}

pub fn records[&l](lg: &l Log) -> [] int {
    return lg.records;
}

pub fn last_ms[&l](lg: &l Log) -> [] int {
    return lg.last_ms;
}

pub fn last_seq[&l](lg: &l Log) -> [] int {
    return lg.last_seq;
}

pub fn broken[&l](lg: &l Log) -> [] bool {
    return lg.broken;
}

// Does `(ms, seq)` come strictly after the log's last id?
fn after[&l](lg: &l Log, ms: int, seq: int) -> [] bool {
    if ms != lg.last_ms {
        return ms > lg.last_ms;
    }
    return seq > lg.last_seq;
}

// Append the sealed record `rec` (written with `record.begin`, `record.put_pair` and `record.seal`) whose id is `(ms, seq)`.
// Answers 0, or one of the codes above, or an errno from the write (and the log is then broken). The record is **not**
// durable until `flush` has answered 0.
pub fn append[&l, &r](lg: &!l Log, rec: &r [byte], ms: int, seq: int) -> [file_write] int {
    if lg.broken {
        return is_broken();
    }
    if len(rec) - 4 > lg.max_len {
        return too_long();
    }
    if !after(lg, ms, seq) {
        return id_not_after();
    }
    var done = 0;
    while done < len(rec) {
        match file_write(lg.writer, rec[done..len(rec)]) {
            Done::Ok(n) => {
                done = done + n;
            }
            Done::Failed(e) => {
                lg.broken = true;
                return e;
            }
        }
    }
    lg.size = lg.size + len(rec);
    lg.last_ms = ms;
    lg.last_seq = seq;
    lg.records = lg.records + 1;
    return 0;
}

// Make everything appended so far survive a crash. Answers 0, or the errno, after which the log is broken and a caller must
// not acknowledge anything it had not already acknowledged. Does nothing when there is nothing new.
pub fn flush[&l](lg: &!l Log) -> [file_write] int {
    if lg.broken {
        return is_broken();
    }
    if lg.synced == lg.size {
        return 0;
    }
    match file_sync(lg.writer) {
        Done::Ok(n) => {
            lg.synced = lg.size;
            return 0;
        }
        Done::Failed(e) => {
            lg.broken = true;
            return e;
        }
    }
}

// Read the record at `at` into `buf` (at least `max_len + 4` bytes), looking only at what a flush has covered. Answers
// `(status, total)`: 0 and the record's size, 1 at the end of what is durable, 2 if it is not a whole record (which cannot
// happen in a log this module wrote, and is reported, not hidden).
pub fn read_at[&l, &b](lg: &!l Log, at: int, buf: &!b [byte]) -> [file_read] (int, int) {
    if at >= lg.synced {
        return (1, 0);
    }
    var want = lg.synced - at;
    if want > len(buf) {
        want = len(buf);
    }
    var got = 0;
    match file_pread(lg.reader, at, buf[0..want]) {
        Read::Got(n) => {
            got = n;
        }
        Read::End => {
            return (2, 0);
        }
        Read::Failed(e) => {
            return (2, 0);
        }
    }
    let r = record.check(buf, 0, got, lg.max_len);
    if r.0 == record.ok() {
        return (0, r.1);
    }
    return (2, 0);
}
