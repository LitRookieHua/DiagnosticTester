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


def test_fd_sf_62():
    p = bytes(62)
    assert encode(p, True) == [bytes([0x00, 62]) + p]


def test_fd_63_goes_ff():
    p = bytes(63)
    frames = encode(p, True)
    assert frames[0][:2] == bytes([0x10, 63])
    assert len(frames) == 2
    assert frames[1][:2] == bytes([0x20, 0x01])
    assert frames[1][2:] == p[62:]


def test_fd_12bit_seq():
    p = bytes(300)
    frames = encode(p, True)
    seqs = [((f[0] & 0x0F) << 8) | f[1] for f in frames[1:]]
    assert seqs == [1, 2, 3, 4]


def test_over_4095_rejected():
    import pytest
    with pytest.raises(ValueError):
        encode(bytes(4096), True)
