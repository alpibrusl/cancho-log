edition 5;

import crc;

// How fast CRC-32C runs in lex-sys: it decides whether the checksum is a visible part of an append
// (`docs/design.md` section 10). `rounds` passes over a `size`-byte buffer; answers a value that depends on every
// pass, so the work cannot be dropped. Arguments: size, rounds.
fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(io);
    release(ffi);
    release(fs);
    release(heap);
    release(net);
    release(clock);
    var size = 100;
    var rounds = 1000000;
    borrow args as &g in {
        if arg_count(g) > 1 {
            size = parse(arg(g, 1));
        }
        if arg_count(g) > 2 {
            rounds = parse(arg(g, 2));
        }
    }
    release(args);
    var acc = 0;
    region a {
        let buf = alloc_slice[a](size, byte_of(0));
        var i = 0;
        while i < size {
            buf[i] = byte_of((i * 31 + 7) % 256);
            i = i + 1;
        }
        var r = 0;
        while r < rounds {
            buf[0] = byte_of(r % 256);
            acc = (acc + crc.of(buf)) % 1000003;
            r = r + 1;
        }
    }
    return acc % 256;
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
