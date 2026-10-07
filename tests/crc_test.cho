edition 5;

import std.test;
import crc;

// CRC-32C against the vectors RFC 3720 (iSCSI) section B.4 publishes, the check value every CRC catalogue gives for
// "123456789", and the properties a record's checksum relies on: feeding in pieces equals feeding at once, and a single
// changed bit changes the answer.

fn fill_with[&b](buf: &!b [byte], value: int, n: int) -> [] int {
    var i = 0;
    while i < n {
        buf[i] = byte_of(value);
        i = i + 1;
    }
    return 0;
}

fn test_the_check_value() -> [] int {
    test.assert_eq(crc.of("123456789"), 0xe3069283);
    return 0;
}

fn test_the_empty_message() -> [] int {
    test.assert_eq(crc.of(""), 0);
    return 0;
}

fn test_rfc_3720_thirty_two_zero_bytes() -> [] int {
    region a {
        let buf = alloc_slice[a](32, byte_of(0));
        test.assert_eq(crc.of(buf), 0x8a9136aa);
    }
    return 0;
}

fn test_rfc_3720_thirty_two_ff_bytes() -> [] int {
    region a {
        let buf = alloc_slice[a](32, byte_of(0));
        fill_with(buf, 255, 32);
        test.assert_eq(crc.of(buf), 0x62a8ab43);
    }
    return 0;
}

fn test_rfc_3720_ascending_and_descending() -> [] int {
    region a {
        let up = alloc_slice[a](32, byte_of(0));
        let down = alloc_slice[a](32, byte_of(0));
        var i = 0;
        while i < 32 {
            up[i] = byte_of(i);
            down[i] = byte_of(31 - i);
            i = i + 1;
        }
        test.assert_eq(crc.of(up), 0x46dd794e);
        test.assert_eq(crc.of(down), 0x113fdb5c);
    }
    return 0;
}

// Feeding a message in two pieces, split at every position, gives the answer for the whole.
fn test_pieces_equal_the_whole() -> [] int {
    region a {
        let buf = alloc_slice[a](40, byte_of(0));
        var i = 0;
        while i < 40 {
            buf[i] = byte_of((i * 7 + 3) % 256);
            i = i + 1;
        }
        let whole = crc.of(buf);
        var split = 0;
        while split <= 40 {
            let first = crc.update(crc.start(), buf[0..split]);
            let both = crc.update(first, buf[split..40]);
            test.assert_eq(crc.finish(both), whole);
            split = split + 1;
        }
    }
    return 0;
}

// Every single bit of a message, flipped, changes the checksum: the property a torn or damaged record relies on.
fn test_every_single_bit_flip_is_seen() -> [] int {
    region a {
        let buf = alloc_slice[a](24, byte_of(0));
        var i = 0;
        while i < 24 {
            buf[i] = byte_of((i * 11 + 5) % 256);
            i = i + 1;
        }
        let before = crc.of(buf);
        var at = 0;
        while at < 24 {
            var bit = 0;
            while bit < 8 {
                let original = int_of(buf[at]);
                buf[at] = byte_of(original ^ 1 << bit);
                test.assert_ne(crc.of(buf), before);
                buf[at] = byte_of(original);
                bit = bit + 1;
            }
            at = at + 1;
        }
        test.assert_eq(crc.of(buf), before);
    }
    return 0;
}
