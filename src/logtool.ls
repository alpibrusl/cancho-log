edition 5;

import std.io;
import std.buffer;
import crc;
import record;
import segment;

// `logtool` -- the two operations the crash sweep (`tests/sweep.py`, `docs/design.md` section 6.2) needs from the real
// code, as a command line:
//
//     logtool write   <dir> <count> <sync_every> <seed>    write <dir>/0.seg, one record at a time, syncing every
//                                                           `sync_every` records, and print every record's end and
//                                                           every sync, so the sweep knows what was acknowledged
//     logtool recover <dir> active|sealed                   scan <dir>/0.seg; an active segment is cut to its last whole
//                                                           record, a sealed one that is not whole is refused (exit 3)
//
// It is a test tool: it holds the unnarrowed filesystem so that the directory can come from the command line. A server
// would name its directory in its type.

fn max_len() -> [] int {
    return 65536;
}

fn parse[&r](s: &r [byte]) -> [] int {
    var n = 0;
    var i = 0;
    while i < len(s) {
        n = n * 10 + (int_of(s[i]) - 48);
        i = i + 1;
    }
    return n;
}

// `<dir>/0.seg` into `out`; answers its length.
fn seg_path[&r, &o](out: &!o [byte], dir: &r [byte]) -> [] int {
    var i = 0;
    while i < len(dir) {
        out[i] = dir[i];
        i = i + 1;
    }
    let name = "/0.seg";
    var j = 0;
    while j < len(name) {
        out[i + j] = name[j];
        j = j + 1;
    }
    return i + len(name);
}

// Write all of `bytes`, however many `file_write`s it takes. Answers 0, or the errno.
fn write_fully[&f, &r](file: &!f File, bytes: &r [byte]) -> [file_write] int {
    var done = 0;
    while done < len(bytes) {
        match file_write(file, bytes[done..len(bytes)]) {
            Done::Ok(n) => {
                done = done + n;
            }
            Done::Failed(e) => {
                return e;
            }
        }
    }
    return 0;
}

fn say[&i, &r](io: &!i Io, word: &r [byte], value: int) -> [io_write] int {
    io.write_all(io, word);
    io.space(io);
    io.print_int(io, value);
    return 0;
}

fn cmd_write[&c, &i, &r](fs: &c Fs(""), io: &!i Io, dir: &r [byte], count: int, sync_every: int, seed: int) -> [fs_write(""), file_write, io_write] int {
    var status = 0;
    region a {
        let path_buf = alloc_slice[a](4096, byte_of(0));
        let path = path_buf[0..seg_path(path_buf, dir)];
        let out = alloc_slice[a](512, byte_of(0));
        let value = alloc_slice[a](64, byte_of(0));
        match open_write(fs, path) {
            Opened::Failed(e) => {
                status = 100 + e;
            }
            Opened::Ok(opened) => {
                var file = opened;
                borrow mut file as &!h in {
                    var offset = 0;
                    var state = seed;
                    var i = 0;
                    while i < count && status == 0 {
                        // A value of 0 to 39 bytes, from a generator that cannot overflow.
                        state = (state * 7919 + 12345) % 2147483648;
                        let vlen = state / 65536 % 40;
                        var k = 0;
                        while k < vlen {
                            state = (state * 7919 + 12345) % 2147483648;
                            value[k] = byte_of(state / 65536 % 256);
                            k = k + 1;
                        }
                        var p = record.begin(out, 0, i + 1, 0, 2);
                        p = record.put_pair(out, p, "n", value[0..vlen]);
                        p = record.put_pair(out, p, "", "x");
                        let total = record.seal(out, 0, p);
                        status = write_fully(h, out[0..total]);
                        offset = offset + total;
                        say(io, "rec", i);
                        io.space(io);
                        io.print_int(io, offset);
                        io.newline(io);
                        i = i + 1;
                        if sync_every > 0 && i % sync_every == 0 {
                            match file_sync(h) {
                                Done::Ok(n) => {
                                    say(io, "sync", offset);
                                    io.newline(io);
                                }
                                Done::Failed(e) => {
                                    status = 200 + e;
                                }
                            }
                        }
                    }
                    say(io, "end", offset);
                    io.newline(io);
                }
                file_close(file);
            }
        }
    }
    return status;
}

// 0 if the segment is whole or was cut to its last whole record, 3 if it is sealed and not whole, 100+ on an I/O error.
fn cmd_recover[&c, &i, &r, &w](fs: &c Fs(""), io: &!i Io, dir: &r [byte], sealed: bool, window: &!w [byte]) -> [fs_read(""), fs_write(""), file_read, file_write, io_write] int {
    var status = 0;
    region a {
        let path_buf = alloc_slice[a](4096, byte_of(0));
        let path = path_buf[0..seg_path(path_buf, dir)];
        match open_rw(fs, path) {
            Opened::Failed(e) => {
                status = 100 + e;
            }
            Opened::Ok(opened) => {
                var file = opened;
                borrow mut file as &!h in {
                    match file_size(h) {
                        Done::Failed(e) => {
                            status = 110 + e;
                        }
                        Done::Ok(size) => {
                            let r = segment.scan(h, size, window, max_len(), 0 - 1, 0 - 1);
                            say(io, "verdict", r.0);
                            io.space(io);
                            say(io, "valid_end", r.1);
                            io.space(io);
                            say(io, "records", r.2);
                            io.space(io);
                            say(io, "last_ms", r.3);
                            io.space(io);
                            say(io, "size", size);
                            io.newline(io);
                            if r.0 != segment.clean() {
                                if sealed {
                                    status = 3;
                                } else {
                                    match file_truncate(h, r.1) {
                                        Done::Ok(n) => {
                                            match file_sync(h) {
                                                Done::Ok(m) => {
                                                    say(io, "truncated", r.1);
                                                    io.newline(io);
                                                }
                                                Done::Failed(e) => {
                                                    status = 120 + e;
                                                }
                                            }
                                        }
                                        Done::Failed(e) => {
                                            status = 130 + e;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                file_close(file);
            }
        }
    }
    return status;
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(ffi);
    release(net);
    release(clock);
    var status = 2;
    var pool = heap;
    var out = io;
    borrow mut pool as &!hp in {
        var wbuf = buffer.empty(hp, max_len() + 4096);
        region a {
            // Copy the command line out of the borrow: `arg` answers a view that lives only inside it.
            let words = alloc_slice[a](8192, byte_of(0));
            let starts = alloc_slice[a](8, 0);
            let lens = alloc_slice[a](8, 0);
            var count = 0;
            borrow args as &g in {
                var at = 0;
                count = arg_count(g);
                if count > 8 {
                    count = 8;
                }
                var n = 0;
                while n < count {
                    let w = arg(g, n);
                    starts[n] = at;
                    lens[n] = len(w);
                    var k = 0;
                    while k < len(w) && at < 8192 {
                        words[at] = w[k];
                        at = at + 1;
                        k = k + 1;
                    }
                    n = n + 1;
                }
            }
            if count >= 3 {
                let cmd = words[starts[1]..starts[1] + lens[1]];
                let dir = words[starts[2]..starts[2] + lens[2]];
                borrow fs as &c in {
                    borrow mut out as &!o in {
                        if lens[1] == 5 && count >= 6 {
                            status = cmd_write(c, o, dir, parse(words[starts[3]..starts[3] + lens[3]]), parse(words[starts[4]..starts[4] + lens[4]]), parse(words[starts[5]..starts[5] + lens[5]]));
                        } else if lens[1] == 7 && count >= 4 {
                            let sealed = int_of(words[starts[3]]) == 115;
                            borrow mut wbuf as &!wb in {
                                status = cmd_recover(c, o, dir, sealed, buffer.room(wb));
                            }
                        }
                    }
                }
            }
        }
        buffer.drop(hp, wbuf);
    }
    release(args);
    release(fs);
    release(out);
    release(pool);
    return status;
}
