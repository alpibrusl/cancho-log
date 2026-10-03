edition 5;

module record;

import crc;

// `record` -- one entry as it sits in a segment file (`docs/design.md` section 4), and the one question recovery asks of every
// record it meets: is this whole?
//
//     bytes  field
//        4   len      what follows this field: 24 and up, at most the caller's maximum
//        4   crc      CRC-32C of everything after this field
//        8   ms       the entry id's millisecond part  (0 .. 2^63 - 1: this language's `int` is signed)
//        8   seq      the entry id's sequence part     (the same range)
//        4   fields   how many field/value pairs follow
//      ...   pairs    each a u32 length and bytes, twice (the field, then its value)
//
// All integers are little-endian. Nothing here touches a file: bytes in, an answer out, so every case a damaged file can
// produce is tested from bytes (`tests/record_test.ls`) without making a damaged file.
//
// **Reading never traps on what it is given.** A length below the minimum or above the maximum, a record that runs past
// the end of what was read, a checksum that does not match, a pair that overruns its record, an id past 2^63: each is an
// answer (`incomplete` or `bad`), because a recovery that crashed on a damaged file would be the worst thing a recovery
// could do.

// The bytes before the pairs: len, crc, ms, seq, fields.
pub fn header_size() -> [] int {
    return 28;
}

// The smallest value of `len`: a record with no pairs.
pub fn min_len() -> [] int {
    return 24;
}

// `check`'s answers.
pub fn ok() -> [] int {
    return 0;
}

// Not enough bytes are there to say: a torn write at the end of a file looks like this.
pub fn incomplete() -> [] int {
    return 1;
}

// Not a record: a length out of range, a checksum that does not match, a structure that does not add up.
pub fn bad() -> [] int {
    return 2;
}

// ---------------------------------------------------------------------
// Little-endian integers
// ---------------------------------------------------------------------

pub fn put_u32[&o](out: &!o [byte], at: int, value: int) -> [] int {
    out[at] = byte_of(value & 0xff);
    out[at + 1] = byte_of(value >> 8 & 0xff);
    out[at + 2] = byte_of(value >> 16 & 0xff);
    out[at + 3] = byte_of(value >> 24 & 0xff);
    return at + 4;
}

pub fn get_u32[&b](buf: &b [byte], at: int) -> [] int {
    return int_of(buf[at]) | int_of(buf[at + 1]) << 8 | int_of(buf[at + 2]) << 16 | int_of(buf[at + 3]) << 24;
}

// `value` must be in [0, 2^63): the top bit of the eighth byte is never set.
pub fn put_u64[&o](out: &!o [byte], at: int, value: int) -> [] int {
    var i = 0;
    while i < 8 {
        out[at + i] = byte_of(value >> 8 * i & 0xff);
        i = i + 1;
    }
    return at + 8;
}

// The caller has checked that the eighth byte is below 128; see `check`.
pub fn get_u64[&b](buf: &b [byte], at: int) -> [] int {
    var value = 0;
    var i = 7;
    while i >= 0 {
        value = value << 8 | int_of(buf[at + i]);
        i = i - 1;
    }
    return value;
}

// ---------------------------------------------------------------------
// Writing
// ---------------------------------------------------------------------

// Begin a record at `at`: its id and its field count. Answers where the first pair goes. `len` and `crc` are filled in by
// `seal`, once the pairs are there.
pub fn begin[&o](out: &!o [byte], at: int, ms: int, seq: int, fields: int) -> [] int {
    put_u64(out, at + 8, ms);
    put_u64(out, at + 16, seq);
    put_u32(out, at + 24, fields);
    return at + 28;
}

// Append one field/value pair at `at`; answers where the next goes.
pub fn put_pair[&o, &k, &v](out: &!o [byte], at: int, key: &k [byte], value: &v [byte]) -> [] int {
    var p = put_u32(out, at, len(key));
    var i = 0;
    while i < len(key) {
        out[p + i] = key[i];
        i = i + 1;
    }
    p = put_u32(out, p + len(key), len(value));
    i = 0;
    while i < len(value) {
        out[p + i] = value[i];
        i = i + 1;
    }
    return p + len(value);
}

// Finish the record that began at `at` and ends at `end`: write `len` and the checksum. Answers the record's total size.
pub fn seal[&o](out: &!o [byte], at: int, end: int) -> [] int {
    put_u32(out, at, end - at - 4);
    put_u32(out, at + 4, crc.of(out[at + 8..end]));
    return end - at;
}

// ---------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------

// Is there a whole, valid record at `at`, in `buf[..limit]`? Answers `(status, total)`: `ok` and the record's size in
// bytes, `incomplete` or `bad` and 0. `max_len` is the largest `len` the log allows.
//
// The order matters: the length is range-checked before it is believed, the checksum is read only once the whole
// record is there, and the structure is walked only once the checksum agrees, so a damaged record is refused at the
// cheapest check that catches it.
pub fn check[&b](buf: &b [byte], at: int, limit: int, max_len: int) -> [] (int, int) {
    if limit - at < 4 {
        return (incomplete(), 0);
    }
    let length = get_u32(buf, at);
    if length < min_len() || length > max_len {
        return (bad(), 0);
    }
    let total = 4 + length;
    if limit - at < total {
        return (incomplete(), 0);
    }
    let stored = get_u32(buf, at + 4);
    if stored != crc.of(buf[at + 8..at + total]) {
        return (bad(), 0);
    }
    // An id past 2^63 cannot be held in an `int`.
    if int_of(buf[at + 15]) >= 128 || int_of(buf[at + 23]) >= 128 {
        return (bad(), 0);
    }
    // The pairs must exactly fill what is left: `fields` of them, no byte over, none past the end.
    let fields = get_u32(buf, at + 24);
    let end = at + total;
    var p = at + 28;
    var n = 0;
    while n < fields {
        if end - p < 4 {
            return (bad(), 0);
        }
        let key_len = get_u32(buf, p);
        p = p + 4;
        if end - p < key_len + 4 {
            return (bad(), 0);
        }
        p = p + key_len;
        let value_len = get_u32(buf, p);
        p = p + 4;
        // Not strictly needed: a value that overruns is also caught by the `p != end` check below. It is kept so that no
        // step of the walk moves past the record's end, whatever the next check would have said (mutation testing found no
        // test that can tell the two apart, and this is why).
        if end - p < value_len {
            return (bad(), 0);
        }
        p = p + value_len;
        n = n + 1;
    }
    if p != end {
        return (bad(), 0);
    }
    return (ok(), total);
}

// The entry id of the record at `at`, which `check` has passed.
pub fn ms_of[&b](buf: &b [byte], at: int) -> [] int {
    return get_u64(buf, at + 8);
}

pub fn seq_of[&b](buf: &b [byte], at: int) -> [] int {
    return get_u64(buf, at + 16);
}

pub fn fields_of[&b](buf: &b [byte], at: int) -> [] int {
    return get_u32(buf, at + 24);
}

// Where the first pair of the record at `at` begins.
pub fn first_pair(at: int) -> [] int {
    return at + 28;
}

// The field of the pair that begins at `p`, and where its value is and how long: `(key_start, key_len, value_start,
// value_len, next)`.
pub fn pair_at[&b](buf: &b [byte], p: int) -> [] (int, int, int, int, int) {
    let key_len = get_u32(buf, p);
    let value_len = get_u32(buf, p + 4 + key_len);
    let value_start = p + 4 + key_len + 4;
    return (p + 4, key_len, value_start, value_len, value_start + value_len);
}
