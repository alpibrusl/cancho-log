edition 5;

import std.test;
import crc;
import record;

// The record format, from bytes (`docs/design.md` sections 4 and 6): a record written reads back whole; every shorter prefix
// of it is `incomplete`; every single changed bit makes it something other than `ok`; a damaged length, a zeroed page and
// a structure that does not add up are refused, never trapped on; and records laid end to end can be walked.

fn max_len() -> [] int {
    return 1048576;
}

// A record with three pairs at `at` in `out`; answers its total size.
fn write_sample[&o](out: &!o [byte], at: int, ms: int, seq: int) -> [] int {
    var p = record.begin(out, at, ms, seq, 3);
    p = record.put_pair(out, p, "name", "ada");
    p = record.put_pair(out, p, "", "");
    p = record.put_pair(out, p, "k", "a longer value than the others");
    return record.seal(out, at, p);
}

fn test_a_record_reads_back_whole() -> [] int {
    region a {
        let buf = alloc_slice[a](256, byte_of(0));
        let total = write_sample(buf, 0, 1234567, 9);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.ok());
        test.assert_eq(r.1, total);
        test.assert_eq(record.ms_of(buf, 0), 1234567);
        test.assert_eq(record.seq_of(buf, 0), 9);
        test.assert_eq(record.fields_of(buf, 0), 3);
        // The first pair is `name` -> `ada`.
        let p = record.pair_at(buf, record.first_pair(0));
        test.assert_eq(p.1, 4);
        test.assert_eq(p.3, 3);
        test.assert_eq(int_of(buf[p.0]), int_of(byte_of('n')));
        test.assert_eq(int_of(buf[p.2]), int_of(byte_of('a')));
        // The second is empty both ways, and the third's value is as long as it was written.
        let q = record.pair_at(buf, p.4);
        test.assert_eq(q.1, 0);
        test.assert_eq(q.3, 0);
        let s = record.pair_at(buf, q.4);
        test.assert_eq(s.1, 1);
        test.assert_eq(s.3, 30);
        test.assert_eq(s.4, total);
    }
    return 0;
}

// A record with no pairs is the smallest there is, and is valid.
fn test_the_smallest_record() -> [] int {
    region a {
        let buf = alloc_slice[a](64, byte_of(0));
        let p = record.begin(buf, 0, 0, 0, 0);
        let total = record.seal(buf, 0, p);
        test.assert_eq(total, 28);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.ok());
    }
    return 0;
}

// Every proper prefix of a record is `incomplete`, which is what a torn write at the end of a file looks like.
fn test_every_prefix_is_incomplete() -> [] int {
    region a {
        let buf = alloc_slice[a](256, byte_of(0));
        let total = write_sample(buf, 0, 42, 1);
        var k = 0;
        while k < total {
            let r = record.check(buf, 0, k, max_len());
            test.assert_eq(r.0, record.incomplete());
            k = k + 1;
        }
        let whole = record.check(buf, 0, total, max_len());
        test.assert_eq(whole.0, record.ok());
    }
    return 0;
}

// Every single bit of a record, flipped, makes it something other than `ok`. A flip in the length can make it longer than
// what is there (`incomplete`) or out of range (`bad`); a flip anywhere else is caught by the checksum or the structure.
fn test_every_single_bit_flip_is_refused() -> [] int {
    region a {
        let buf = alloc_slice[a](256, byte_of(0));
        let total = write_sample(buf, 0, 42, 1);
        var at = 0;
        while at < total {
            var bit = 0;
            while bit < 8 {
                let original = int_of(buf[at]);
                buf[at] = byte_of(original ^ 1 << bit);
                let r = record.check(buf, 0, total, max_len());
                test.assert_ne(r.0, record.ok());
                buf[at] = byte_of(original);
                bit = bit + 1;
            }
            at = at + 1;
        }
        let whole = record.check(buf, 0, total, max_len());
        test.assert_eq(whole.0, record.ok());
    }
    return 0;
}

// A page of zeros, which is what an unwritten block reads as, is `bad` and not a record of length zero.
fn test_a_zeroed_page_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](4096, byte_of(0));
        let r = record.check(buf, 0, 4096, max_len());
        test.assert_eq(r.0, record.bad());
        // And at an offset in the middle of it.
        let s = record.check(buf, 1000, 4096, max_len());
        test.assert_eq(s.0, record.bad());
    }
    return 0;
}

// A length above the maximum is `bad` however much data follows, and one below the smallest is too.
fn test_a_length_out_of_range_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](256, byte_of(0));
        let total = write_sample(buf, 0, 1, 1);
        // The maximum is smaller than this record.
        let r = record.check(buf, 0, total, total - 5);
        test.assert_eq(r.0, record.bad());
        // Exactly the record's own length is allowed.
        let s = record.check(buf, 0, total, total - 4);
        test.assert_eq(s.0, record.ok());
        // A length of 23 is below the smallest.
        record.put_u32(buf, 0, 23);
        let t = record.check(buf, 0, total, max_len());
        test.assert_eq(t.0, record.bad());
        // And the largest a u32 can say is far above any maximum, and is `bad`, not a trap or a wrap.
        record.put_u32(buf, 0, 4294967295);
        let u = record.check(buf, 0, total, max_len());
        test.assert_eq(u.0, record.bad());
    }
    return 0;
}

// A record whose checksum is right but whose pairs do not add up is `bad`: `fields` says five, there are three. This is a
// writer's bug or a forgery, not a torn write, and the checksum alone would not catch it.
fn test_a_checksum_that_matches_a_structure_that_does_not() -> [] int {
    region a {
        let buf = alloc_slice[a](256, byte_of(0));
        var p = record.begin(buf, 0, 1, 1, 5);
        p = record.put_pair(buf, p, "a", "b");
        p = record.put_pair(buf, p, "c", "d");
        p = record.put_pair(buf, p, "e", "f");
        let total = record.seal(buf, 0, p);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.bad());
        // And the other way: `fields` says one, there are three pairs' worth of bytes.
        var q = record.begin(buf, 100, 1, 1, 1);
        q = record.put_pair(buf, q, "a", "b");
        q = record.put_pair(buf, q, "c", "d");
        let total2 = record.seal(buf, 100, q);
        let s = record.check(buf, 100, 100 + total2, max_len());
        test.assert_eq(s.0, record.bad());
    }
    return 0;
}

// A pair whose declared length runs past the record is `bad`, with a valid checksum over the lie.
fn test_a_pair_that_overruns_its_record_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](256, byte_of(0));
        var p = record.begin(buf, 0, 1, 1, 1);
        p = record.put_pair(buf, p, "key", "value");
        // The key's length becomes huge.
        record.put_u32(buf, record.first_pair(0), 4000000000);
        let total = record.seal(buf, 0, p);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.bad());
    }
    return 0;
}

// An id with the top bit set is `bad`: this language's `int` is signed, and the log says so rather than wrap.
fn test_an_id_past_two_to_the_sixty_three_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](64, byte_of(0));
        let p = record.begin(buf, 0, 1, 1, 0);
        buf[15] = byte_of(128);
        let total = record.seal(buf, 0, p);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.bad());
    }
    return 0;
}

// Records laid end to end can be walked with the size `check` answers, and a record cut off at the end is `incomplete`.
fn test_records_end_to_end() -> [] int {
    region a {
        let buf = alloc_slice[a](1024, byte_of(0));
        var end = 0;
        var i = 0;
        while i < 5 {
            end = end + write_sample(buf, end, 100 + i, i);
            i = i + 1;
        }
        var at = 0;
        var seen = 0;
        while at < end {
            let r = record.check(buf, at, end, max_len());
            test.assert_eq(r.0, record.ok());
            test.assert_eq(record.ms_of(buf, at), 100 + seen);
            at = at + r.1;
            seen = seen + 1;
        }
        test.assert_eq(seen, 5);
        test.assert_eq(at, end);
        // Cut the last record short: the first four are walked, the fifth is `incomplete`.
        let cut = end - 3;
        at = 0;
        seen = 0;
        var last = record.ok();
        while at < cut && last == record.ok() {
            let r = record.check(buf, at, cut, max_len());
            last = r.0;
            if r.0 == record.ok() {
                at = at + r.1;
                seen = seen + 1;
            }
        }
        test.assert_eq(seen, 4);
        test.assert_eq(last, record.incomplete());
    }
    return 0;
}

// Pseudo-random bytes are never a record and never trap, from every offset. A fixed seed, so a failure is reproducible.
fn test_random_bytes_are_never_ok_and_never_trap() -> [] int {
    region a {
        let buf = alloc_slice[a](512, byte_of(0));
        var seed = 12345;
        var round = 0;
        while round < 200 {
            var i = 0;
            while i < 512 {
                seed = (seed * 7919 + 12345) % 2147483648;
                buf[i] = byte_of(seed / 65536 % 256);
                i = i + 1;
            }
            var at = 0;
            while at < 512 {
                let r = record.check(buf, at, 512, max_len());
                test.assert_ne(r.0, record.ok());
                at = at + 1;
            }
            round = round + 1;
        }
    }
    return 0;
}

// A record whose `len` is too small to hold a header, with a checksum that is right over those few bytes, is `bad` and is
// refused before anything past its end is read. The buffer is cut to exactly the record's bytes, so reading the header
// fields of a record that is not there would trap, and the test would fail by trapping.
fn test_a_valid_checksum_over_too_few_bytes_is_bad() -> [] int {
    region a {
        // `len` is 8, far too small, and 23, one byte short of a header with no pairs.
        let buf = alloc_slice[a](27, byte_of(0));
        record.put_u32(buf, 0, 8);
        record.put_u32(buf, 4, crc.of(buf[8..12]));
        let r = record.check(buf[0..12], 0, 12, max_len());
        test.assert_eq(r.0, record.bad());
        record.put_u32(buf, 0, 23);
        record.put_u32(buf, 4, crc.of(buf[8..27]));
        let s = record.check(buf[0..27], 0, 27, max_len());
        test.assert_eq(s.0, record.bad());
    }
    return 0;
}

// A pair whose key runs to the very end of the record, leaving no room for its value's length, is `bad` without reading
// past the end. Again the buffer is exactly the record.
fn test_a_key_that_leaves_no_room_for_its_value_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](64, byte_of(0));
        var p = record.begin(buf, 0, 1, 1, 1);
        // A key of three bytes whose length field claims all of them, and nothing after.
        p = record.put_u32(buf, p, 3);
        buf[p] = byte_of('a');
        buf[p + 1] = byte_of('b');
        buf[p + 2] = byte_of('c');
        let total = record.seal(buf, 0, p + 3);
        let r = record.check(buf[0..total], 0, total, max_len());
        test.assert_eq(r.0, record.bad());
        // And a value whose declared length is one more than is there.
        var q = record.begin(buf, 0, 1, 1, 1);
        q = record.put_u32(buf, q, 1);
        buf[q] = byte_of('k');
        q = record.put_u32(buf, q + 1, 5);
        buf[q] = byte_of('v');
        let total2 = record.seal(buf, 0, q + 1);
        let s = record.check(buf[0..total2], 0, total2, max_len());
        test.assert_eq(s.0, record.bad());
    }
    return 0;
}

// The id's top bit is refused in either half, not only the first.
fn test_a_sequence_part_past_two_to_the_sixty_three_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](64, byte_of(0));
        let p = record.begin(buf, 0, 1, 1, 0);
        buf[23] = byte_of(128);
        let total = record.seal(buf, 0, p);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.bad());
    }
    return 0;
}

// A record that says it has a pair and has no bytes for it is `bad`, without reading past its end (the buffer is cut to
// exactly the record).
fn test_a_field_count_with_no_pairs_is_bad() -> [] int {
    region a {
        let buf = alloc_slice[a](64, byte_of(0));
        let p = record.begin(buf, 0, 1, 1, 1);
        let total = record.seal(buf, 0, p);
        let r = record.check(buf[0..total], 0, total, max_len());
        test.assert_eq(r.0, record.bad());
    }
    return 0;
}

// An id that uses every byte reads back as written: the byte order is right at both ends.
fn test_an_id_that_uses_every_byte_round_trips() -> [] int {
    region a {
        let buf = alloc_slice[a](64, byte_of(0));
        let p = record.begin(buf, 0, 0x1122334455667788, 0x0102030405060708, 0);
        let total = record.seal(buf, 0, p);
        let r = record.check(buf, 0, total, max_len());
        test.assert_eq(r.0, record.ok());
        test.assert_eq(record.ms_of(buf, 0), 0x1122334455667788);
        test.assert_eq(record.seq_of(buf, 0), 0x0102030405060708);
        // And the bytes are little-endian: the low byte first.
        test.assert_eq(int_of(buf[8]), 0x88);
        test.assert_eq(int_of(buf[15]), 0x11);
    }
    return 0;
}
