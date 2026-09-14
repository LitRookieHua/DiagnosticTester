import pytest

from isotp import encode


def test_classic_sf_7bytes():
    p = bytes.fromhex("22F195AABBCCDD")
    assert encode(p, False) == [bytes([7]) + p]


def test_classic_8bytes_goes_ff_cf():
    p = bytes.fromhex("22F190AABBCCDDEE")
    frames = encode(p, False)
    assert frames[0] == bytes([0x10, 8]) + p[:6]
    assert frames[1] == bytes([0x21]) + p[6:]


def test_classic_multiframe_seq():
    p = bytes(range(30))
    frames = encode(p, False)
    assert frames[0][:2] == bytes([0x10, 30])
    seqs = [f[0] & 0x0F for f in frames[1:]]
    assert seqs == [1, 2, 3, 4]


def test_fd_sf_small_uses_classic_form():
    p = bytes.fromhex("22F195")
    assert encode(p, True) == [bytes([3]) + p]


def test_fd_sf_62_escape_form():
    p = bytes(62)
    assert encode(p, True) == [bytes([0x00, 62]) + p]


def test_fd_63_goes_ff_cf():
    p = bytes(63)
    frames = encode(p, True)
    assert frames[0][:2] == bytes([0x10, 63])
    assert len(frames) == 2
    assert frames[1] == bytes([0x21]) + p[62:]


def test_fd_cf_63_byte_chunks():
    p = bytes(200)  # FF 62 + CF 63 + CF 63 + CF 12
    frames = encode(p, True)
    assert len(frames) == 4
    seqs = [f[0] & 0x0F for f in frames[1:]]
    assert seqs == [1, 2, 3]
    assert len(frames[1]) == 64  # 1 字节 PCI + 63 字节载荷
    assert frames[3] == bytes([0x23]) + p[188:]


def test_over_4095_rejected():
    with pytest.raises(ValueError):
        encode(bytes(4096), True)
    with pytest.raises(ValueError):
        encode(bytes(4096), False)


def test_empty_payload_rejected():
    with pytest.raises(ValueError):
        encode(b"", False)
