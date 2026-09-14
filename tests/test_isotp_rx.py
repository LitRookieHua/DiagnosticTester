from isotp import RxAssembler


def test_classic_sf():
    a = RxAssembler(False)
    payload = bytes.fromhex("62F195")
    assert a.feed(bytes([3]) + payload, 0.0) == payload


def test_classic_ff_cf():
    a = RxAssembler(False)
    p = bytes.fromhex("22F190AABBCCDDEE")
    assert a.feed(bytes([0x10, 8]) + p[:6], 0.0) is None
    assert a.feed(bytes([0x21]) + p[6:], 1.0) == p


def test_classic_cf_timeout_expired():
    a = RxAssembler(False)
    a.feed(bytes([0x10, 8]) + b"123456", 0.0)
    assert a.expired(0.5, n_cr_s=1.0) is False
    assert a.expired(2.0, n_cr_s=1.0) is True


def test_fd_ff_cf():
    a = RxAssembler(True)
    p = bytes(range(63))
    assert a.feed(bytes([0x10, 63]) + p[:62], 0.0) is None
    assert a.feed(bytes([0x21]) + p[62:], 1.0) == p


def test_fd_sf_both_forms():
    a = RxAssembler(True)
    assert a.feed(bytes([0x00, 3]) + b"\x62\xf1\x95", 0.0) == b"\x62\xf1\x95"  # 转义形式
    assert a.feed(bytes([3]) + b"\x50\x03\x00", 0.0) == b"\x50\x03\x00"      # 经典形式


def test_wrong_seq_discards_whole_message():
    a = RxAssembler(False)
    a.feed(bytes([0x10, 8]) + b"123456", 0.0)
    assert a.feed(bytes([0x22]) + b"xy", 1.0) is None
    assert a.total is None  # 整个报文作废


def test_padding_sliced_by_total():
    a = RxAssembler(False)
    p = bytes.fromhex("62F195AABBCCDDEE")
    a.feed(bytes([0x10, 8]) + p[:6], 0.0)
    padded_cf = bytes([0x21]) + p[6:] + b"\x00\x00"
    assert a.feed(padded_cf, 1.0) == p


def test_cf_without_ff_ignored():
    a = RxAssembler(False)
    assert a.feed(bytes([0x21]) + b"1234567", 0.0) is None


def test_malformed_1byte_fd_zero():
    a = RxAssembler(True)
    assert a.feed(b"\x00", 0.0) is None


def test_malformed_1byte_ff():
    for is_fd in (False, True):
        a = RxAssembler(is_fd)
        assert a.feed(b"\x10", 0.0) is None


def test_sf_mid_reassembly_resets():
    a = RxAssembler(False)
    a.feed(bytes([0x10, 8]) + b"123456", 0.0)
    assert a.feed(bytes([3]) + b"abc", 1.0) == b"abc"
    assert a.feed(bytes([0x21]) + b"def", 2.0) is None  # 状态已复位，游离 CF 丢弃
