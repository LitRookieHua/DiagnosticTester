import threading
import time

import pytest

from isotp import IsoTpTransport, IsoTpError, _decode_stmin
from mock_driver import MockCanDriver


def wait_until(cond, timeout_s=2.0, interval=0.01):
    """轮询等待条件成立（有 timeout_s 上限，不挂死）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


def cf_count(mock):
    return sum(1 for f in mock.sent if f.data[0] >> 4 == 0x2)


def cf_count_stable(mock, quiet_s=0.15, timeout_s=1.5):
    """轮询直到 CF 数量在 quiet_s 内不再增长（说明发送方停住等 FC）。"""
    n = cf_count(mock)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(quiet_s)
        cur = cf_count(mock)
        if cur == n:
            return n
        n = cur
    return n


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
        assert mock.sent[0].data == bytes([3, 0x22, 0xF1, 0x95]) + bytes(4)  # SF 补齐到 8 字节
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
        # 轮询等待 pump 处理 FF 并回流控帧（替代固定 sleep，消除时序抖动）
        assert wait_until(
            lambda: any(f.can_id == 0x70C and f.data[0] == 0x30 for f in mock.sent))
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


def test_send_bs_window_waits_fc():
    """发送方向 BS 窗口：FC1(BS=2) 后只发 CF1-CF2，等 FC2 发 CF3-CF4，等 FC3 发 CF5-CF6。"""
    mock = MockCanDriver()
    replied = [False]

    def on_send(frame):
        # FF 只自动回一次 FC(BS=2, STmin=0)，后续 FC 由测试手动推
        if (frame.can_id == 0x70C and frame.data and frame.data[0] >> 4 == 0x1
                and not replied[0]):
            replied[0] = True
            mock.push_rx(0x78C, bytes([0x30, 0x02, 0x00]))

    mock.on_send = on_send
    t = IsoTpTransport(mock, tx_id=0x70C, rx_id=0x78C, is_fd=False, n_bs_s=2.0)
    t.start()
    result = []

    def run():
        try:
            t.send_payload(bytes(range(43)))
            result.append("ok")
        except Exception as e:  # 把发送线程内的异常带回主线程断言
            result.append(e)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    try:
        assert wait_until(lambda: cf_count(mock) >= 2)
        assert cf_count_stable(mock) == 2  # 发完 CF1-CF2 停住等 FC2
        mock.push_rx(0x78C, bytes([0x30, 0x02, 0x00]))  # FC2
        assert wait_until(lambda: cf_count(mock) >= 4)
        assert cf_count_stable(mock) == 4  # 发完 CF3-CF4 停住等 FC3
        mock.push_rx(0x78C, bytes([0x30, 0x02, 0x00]))  # FC3
        assert wait_until(lambda: cf_count(mock) >= 6)
        th.join(timeout=2.0)
        assert result == ["ok"]
    finally:
        t.stop()
        th.join(timeout=2.5)


def test_fc_wait_continues():
    """FC 0x31（WT）一次后再回 0x30 CTS，发送继续并完成。"""
    mock = MockCanDriver()

    def on_send(frame):
        if frame.can_id == 0x70C and frame.data and frame.data[0] >> 4 == 0x1:
            mock.push_rx(0x78C, bytes([0x31, 0x00, 0x00]))  # 先回 WT
            # 模拟 ECU 就绪后补发 CTS（BS=0, STmin=0）
            threading.Timer(0.05, lambda: mock.push_rx(0x78C, bytes([0x30, 0x00, 0x00]))).start()

    mock.on_send = on_send
    t = IsoTpTransport(mock, tx_id=0x70C, rx_id=0x78C, is_fd=False, n_bs_s=2.0)
    t.start()
    try:
        t.send_payload(bytes(range(43)))
        assert [f.data[0] & 0x0F for f in mock.sent if f.data[0] >> 4 == 0x2] == [1, 2, 3, 4, 5, 6]
    finally:
        t.stop()


def test_fc_overflow_raises():
    """FC 0x32（OVFLW）中止发送并抛 IsoTpError。"""
    mock = MockCanDriver()

    def on_send(frame):
        if frame.can_id == 0x70C and frame.data and frame.data[0] >> 4 == 0x1:
            mock.push_rx(0x78C, bytes([0x32, 0x00, 0x00]))

    mock.on_send = on_send
    t = IsoTpTransport(mock, tx_id=0x70C, rx_id=0x78C, is_fd=False, n_bs_s=2.0)
    t.start()
    try:
        with pytest.raises(IsoTpError):
            t.send_payload(bytes(range(43)))
    finally:
        t.stop()


def test_nbs_timeout_raises():
    """FF 后无 FC 到达，N_Bs 超时抛 TimeoutError。"""
    mock = MockCanDriver()
    t = IsoTpTransport(mock, tx_id=0x70C, rx_id=0x78C, is_fd=False, n_bs_s=0.3)
    t.start()
    try:
        with pytest.raises(TimeoutError):
            t.send_payload(bytes(range(43)))
    finally:
        t.stop()


def test_decode_stmin():
    assert _decode_stmin(5) == 0.005
    assert _decode_stmin(0xF1) == 0.0001
    assert _decode_stmin(0x80) == 0.0


def test_recv_bs_nonzero_sends_next_fc():
    """接收方向 fc_bs=2：收满一个块后主动补发 FC，对端才能继续发 CF。"""
    mock = MockCanDriver()
    t = IsoTpTransport(mock, tx_id=0x70C, rx_id=0x78C, is_fd=False, fc_bs=2)
    t.start()
    try:
        p = bytes(range(43))
        mock.push_rx(0x78C, bytes([0x10, 43]) + p[:6])
        mock.push_rx(0x78C, bytes([0x21]) + p[6:13])
        mock.push_rx(0x78C, bytes([0x22]) + p[13:20])
        # FF 的 FC + 收满 BS=2 后补发的第二个 FC
        assert wait_until(
            lambda: sum(1 for f in mock.sent if f.data[0] == 0x30) >= 2)
        seq = 3
        for k in range(20, 43, 7):
            mock.push_rx(0x78C, bytes([0x20 | seq]) + p[k:k + 7])
            seq += 1
        assert t.recv_payload(2.0) == p
    finally:
        t.stop()
