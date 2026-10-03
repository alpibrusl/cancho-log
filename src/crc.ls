edition 5;

module crc;

// `crc` -- CRC-32C (Castagnoli), the checksum a record carries so that recovery can tell a whole record from a torn one
// (`docs/design.md` section 4). Reflected polynomial 0x82f63b78, initial value and final xor 0xffffffff: the one iSCSI
// (RFC 3720), ext4 metadata and most logs use, and the one whose published test vectors the tests check.
//
// A checksum is 32 bits held in an `int`: every value here is in [0, 0xffffffff], so the shifts and xors never meet a
// sign bit and the checked arithmetic never has anything to trap on.

// The 256-entry table, computed when the program is compiled.
static crc_table: [int] {
    let t = alloc_slice[static](256, 0);
    var i = 0;
    while i < 256 {
        var c = i;
        var k = 0;
        while k < 8 {
            if c & 1 == 1 {
                c = c >> 1 ^ 0x82f63b78;
            } else {
                c = c >> 1;
            }
            k = k + 1;
        }
        t[i] = c;
        i = i + 1;
    }
    return t;
}

// The state before any byte.
pub fn start() -> [] int {
    return 0xffffffff;
}

// Feed `data` into a running state.
pub fn update[&b](state: int, data: &b [byte]) -> [] int {
    var c = state;
    var i = 0;
    while i < len(data) {
        c = c >> 8 ^ crc_table[(c ^ int_of(data[i])) & 0xff];
        i = i + 1;
    }
    return c;
}

// The checksum of a state.
pub fn finish(state: int) -> [] int {
    return state ^ 0xffffffff;
}

// The checksum of `data` in one call.
pub fn of[&b](data: &b [byte]) -> [] int {
    return finish(update(start(), data));
}
