import time

from isotp import IsoTpTransport
from mock_driver import MockCanDriver


def make_env(is_fd=False):
    mock = MockCanDriver()

    def on_send(frame):
        # 模拟 ECU：收到 FF 回 FC（CTS, BS=0, STmin=0）
        if frame.can_id == 0x70C and frame.data and frame.data[0] >> 4 == 0x1:
            mock.push_rx(0x78C, bytes([0x30, 0x00, 0x00]), is_fd)

    mock.on_send = on_send
    t = IsoTpTransport(mock, tx_id=0x70C, rx_id=0x78C, is_fd=is_fd)
    t.start()
    return mock, t


def test_send_sf_classic():
    mock, t = make_env()
    try:
        t.send_payload(bytes.fromhex("22F195"))
        assert mock.sent[0].can_id == 0x70C
        assert mock.sent[0].data == bytes([3, 0x22, 0xF1, 0x95])
    finally:
        t.stop()


def test_send_multiframe_classic_waits_fc():
    mock, t = make_env()
    try:
        p = bytes(range(43))
        t.send_payload(p)
        ff = mock.sent[0]
        assert ff.data[:2] == bytes([0x10, 43])
        assert ff.data[2:] == p[:6]
        cfs = mock.sent[1:]
        assert len(cfs) == 6
        assert cfs[0].data[0] == 0x21
        assert cfs[1].data[0] == 0x22
    finally:
        t.stop()


def test_send_multiframe_fd():
    mock, t = make_env(is_fd=True)
    try:
        p = bytes(range(63))
        t.send_payload(p)
        assert mock.sent[0].is_fd is True
        assert mock.sent[0].bitrate_switch is True
        assert mock.sent[0].data[:2] == bytes([0x10, 63])
        assert mock.sent[1].data == bytes([0x21]) + p[62:]
    finally:
        t.stop()


def test_recv_multiframe_sends_fc_and_assembles():
    mock, t = make_env()
    try:
        p = bytes(range(43))
        mock.push_rx(0x78C, bytes([0x10, 43]) + p[:6])
        time.sleep(0.3)  # 等 pump 处理 FF 并回流控帧
        assert any(f.can_id == 0x70C and f.data[0] == 0x30 for f in mock.sent)
        seq = 1
        for k in range(6, 43, 7):
            mock.push_rx(0x78C, bytes([0x20 | seq]) + p[k:k + 7])
            seq += 1
        assert t.recv_payload(2.0) == p
    finally:
        t.stop()


def test_ignores_other_ids():
    mock, t = make_env()
    try:
        mock.push_rx(0x123, bytes([1, 0x42]))
        assert t.recv_payload(0.2) is None
    finally:
        t.stop()


def test_tap_sees_all_frames():
    seen = []
    mock, t = make_env()
    try:
        t.tap = lambda frame, is_rx: seen.append((frame.can_id, is_rx))
        t.send_payload(bytes.fromhex("1003"))
        mock.push_rx(0x999, bytes([1, 0x00]))
        mock.push_rx(0x78C, bytes([3, 0x50, 0x03, 0x00]))
        t.recv_payload(2.0)
        assert (0x70C, False) in seen
        assert (0x999, True) in seen
        assert (0x78C, True) in seen
    finally:
        t.stop()
