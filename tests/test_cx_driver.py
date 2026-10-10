"""CxCanFdDriver 单元测试：注入 fake cxcanfd 模块，不依赖真 DLL/硬件。"""
import types
from ctypes import Structure, Union, c_ubyte, c_uint

import pytest

import can_driver
from can_driver import CanFrame


class _CanFdInit(Structure):
    _fields_ = [("mode", c_ubyte)]


class _ConfigUnion(Union):
    _fields_ = [("canfd", _CanFdInit)]


class FakeInitConfig(Structure):
    _fields_ = [("can_type", c_uint), ("config", _ConfigUnion)]


class FakeCanFrame(Structure):
    _fields_ = [("can_id", c_uint), ("can_dlc", c_ubyte), ("data", c_ubyte * 8)]


class FakeCanFdFrame(Structure):
    _fields_ = [("can_id", c_uint), ("len", c_ubyte), ("brs", c_ubyte),
                ("data", c_ubyte * 64)]


class FakeTransmitData(Structure):
    _fields_ = [("frame", FakeCanFrame), ("transmit_type", c_uint)]


class FakeTransmitFdData(Structure):
    _fields_ = [("frame", FakeCanFdFrame), ("transmit_type", c_uint)]


class FakeReceiveData(Structure):
    _fields_ = [("frame", FakeCanFrame)]


class FakeReceiveFdData(Structure):
    _fields_ = [("frame", FakeCanFdFrame)]


class FakeZCAN:
    """记录调用；ret 可按方法名覆写返回值模拟失败。"""

    def __init__(self):
        self.calls = []
        self.ret = {"OpenDevice": 0x1111, "InitCAN": 0x2222}
        self.fd_frames = []      # GetReceiveNum(TYPE_CANFD) 队列
        self.can_frames = []     # GetReceiveNum(TYPE_CAN) 队列
        self.transmitted = []    # ("fd"/"can", id, len, data[, brs])
        self.rcv_ret = 1        # Receive/ReceiveFD 返回值（可改负数模拟错误码）
        self.getnum_ret = None  # GetReceiveNum 覆写（None=按队列长度）

    def _rec(self, name, *args):
        self.calls.append((name, args))
        return self.ret.get(name, 1)

    def OpenDevice(self, *a):
        return self._rec("OpenDevice", *a)

    def CloseDevice(self, *a):
        return self._rec("CloseDevice", *a)

    def SetAbitBaud(self, *a):
        return self._rec("SetAbitBaud", *a)

    def SetDbitBaud(self, *a):
        return self._rec("SetDbitBaud", *a)

    def SetCANFDStandard(self, *a):
        return self._rec("SetCANFDStandard", *a)

    def InitCAN(self, *a):
        return self._rec("InitCAN", *a)

    def StartCAN(self, *a):
        return self._rec("StartCAN", *a)

    def ResetCAN(self, *a):
        return self._rec("ResetCAN", *a)

    def GetReceiveNum(self, chn, can_type):
        if self.getnum_ret is not None:
            return self.getnum_ret
        return len(self.fd_frames) if can_type == 1 else len(self.can_frames)

    def ReceiveFD(self, chn, msgs, n, timeout):
        spec = self.fd_frames.pop(0)
        f = msgs[0].frame
        f.can_id, f.len, f.brs = spec["id"], spec["len"], spec.get("brs", 0)
        for i, b in enumerate(spec["data"]):
            f.data[i] = b
        return self.rcv_ret

    def Receive(self, chn, msgs, n, timeout):
        spec = self.can_frames.pop(0)
        f = msgs[0].frame
        f.can_id, f.can_dlc = spec["id"], len(spec["data"])
        for i, b in enumerate(spec["data"]):
            f.data[i] = b
        return self.rcv_ret

    def TransmitFD(self, chn, msgs, n):
        for i in range(n):
            f = msgs[i].frame
            self.transmitted.append(
                ("fd", f.can_id, f.len, bytes(f.data[:f.len]), int(f.brs)))
        return n

    def Transmit(self, chn, msgs, n):
        for i in range(n):
            f = msgs[i].frame
            self.transmitted.append(
                ("can", f.can_id, f.can_dlc, bytes(f.data[:f.can_dlc])))
        return n


@pytest.fixture
def fake_cx(monkeypatch):
    zcan = FakeZCAN()
    fake = types.SimpleNamespace(
        VCI_USBCAN2=41, TYPE_CAN=0, TYPE_CANFD=1,
        ZCAN_CHANNEL_INIT_CONFIG=FakeInitConfig,
        ZCAN_Transmit_Data=FakeTransmitData,
        ZCAN_TransmitFD_Data=FakeTransmitFdData,
        ZCAN_Receive_Data=FakeReceiveData,
        ZCAN_ReceiveFD_Data=FakeReceiveFdData,
        ZCAN=lambda: zcan,
    )
    monkeypatch.setattr(can_driver, "import_cxcanfd", lambda: fake)
    return zcan


# ---------- 构造与初始化 ----------

def test_init_sequence(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver(channel=1, baudrate=500000, data_baudrate=2000000)
    try:
        assert [c[0] for c in fake_cx.calls] == [
            "OpenDevice", "SetAbitBaud", "SetDbitBaud",
            "SetCANFDStandard", "InitCAN", "StartCAN"]
        assert fake_cx.calls[:4] == [
            ("OpenDevice", (41, 0, 0)),
            ("SetAbitBaud", (0x1111, 1, 500000)),
            ("SetDbitBaud", (0x1111, 1, 2000000)),
            ("SetCANFDStandard", (0x1111, 1, 0)),
        ]
        assert fake_cx.calls[5] == ("StartCAN", (0x2222,))
        cfg = fake_cx.calls[4][1][2]
        assert cfg.can_type == 1  # TYPE_CANFD
        assert cfg.config.canfd.mode == 0  # 正常模式
    finally:
        d.close()


def test_open_device_fail(fake_cx):
    from can_driver import CxCanFdDriver
    fake_cx.ret["OpenDevice"] = 0
    with pytest.raises(RuntimeError, match="打开 CX-USBCANFD#0 失败"):
        CxCanFdDriver()


def test_startcan_fail_closes_device(fake_cx):
    from can_driver import CxCanFdDriver
    fake_cx.ret["StartCAN"] = 0
    with pytest.raises(RuntimeError, match="启动失败"):
        CxCanFdDriver()
    assert ("CloseDevice", (0x1111,)) in fake_cx.calls


def test_unknown_device_name(fake_cx):
    from can_driver import CxCanFdDriver
    with pytest.raises(RuntimeError, match="不支持的设备"):
        CxCanFdDriver(device="foo")


# ---------- 发送 ----------

def test_send_fd_pads_short_to_8(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        d.send(CanFrame(0x70C, b"\x22\xf1\x95", is_fd=True))
        kind, cid, ln, data, brs = fake_cx.transmitted[0]
        assert (kind, cid, ln, brs) == ("fd", 0x70C, 8, 0)
        assert data == b"\x22\xf1\x95" + b"\x00" * 5
    finally:
        d.close()


def test_send_fd_dlc_ladder(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        payload = bytes(range(9))  # 9 字节补到 12
        d.send(CanFrame(0x70C, payload, is_fd=True))
        kind, cid, ln, data, brs = fake_cx.transmitted[0]
        assert (kind, ln) == ("fd", 12)
        assert data == payload + b"\x00" * 3
    finally:
        d.close()


def test_send_fd_brs_flag(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        d.send(CanFrame(0x70C, b"\x02\x10\x03\x00" * 2, is_fd=True,
                        bitrate_switch=True))
        assert fake_cx.transmitted[0][4] == 1
    finally:
        d.close()


def test_send_classic_over_8_raises(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver(fd=False)
    try:
        with pytest.raises(ValueError, match="经典 CAN 帧最长 8 字节"):
            d.send(CanFrame(0x70C, b"\x01" * 9, is_fd=False))
    finally:
        d.close()


def test_send_mode_mismatch_raises(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver(fd=True)
    try:
        with pytest.raises(ValueError, match="帧模式不匹配"):
            d.send(CanFrame(0x70C, b"\x22", is_fd=False))
    finally:
        d.close()


# ---------- 接收与关闭 ----------

def test_recv_fd_frame(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        fake_cx.fd_frames.append({"id": 0x78C, "len": 8, "brs": 1,
                                  "data": b"\x06\xf1\x95\x01\x02\x03\x04\x05"})
        f = d.recv(timeout_s=2)
        assert f is not None
        assert f.can_id == 0x78C and f.is_fd and f.bitrate_switch
        assert f.data == b"\x06\xf1\x95\x01\x02\x03\x04\x05"
    finally:
        d.close()


def test_recv_classic_frame(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        fake_cx.can_frames.append({"id": 0x78C, "data": b"\x02\x10\x03"})
        f = d.recv(timeout_s=2)
        assert f is not None
        assert f.can_id == 0x78C and not f.is_fd
        assert f.data == b"\x02\x10\x03"
    finally:
        d.close()


def test_recv_ignores_fd_queue_when_classic_mode(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver(fd=False)
    try:
        fake_cx.fd_frames.append({"id": 0x78C, "len": 8, "brs": 1,
                                  "data": b"\x06" * 8})
        fake_cx.can_frames.append({"id": 0x78C, "data": b"\x02\x10\x03"})
        f = d.recv(timeout_s=2)
        assert not f.is_fd
        assert fake_cx.fd_frames  # FD 队列原样保留，未被消费
    finally:
        d.close()


def test_close_resets_and_closes(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    d.close()
    names = [c[0] for c in fake_cx.calls]
    assert names[-2:] == ["ResetCAN", "CloseDevice"]


# ---------- 接收返回值边界（负数错误码） ----------

def test_recv_negative_receive_ret_no_phantom_frame(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        fake_cx.rcv_ret = -1
        fake_cx.fd_frames.append({"id": 0x78C, "len": 8, "brs": 0,
                                  "data": b"\x06" * 8})
        fake_cx.fd_frames.append({"id": 0x78D, "len": 8, "brs": 0,
                                  "data": b"\x07" * 8})
        f = d.recv(timeout_s=0.3)
        assert f is None  # 负返回值不产生幻影帧
    finally:
        d.close()


def test_recv_negative_getreceivenum_keeps_thread_alive(fake_cx):
    from can_driver import CxCanFdDriver
    d = CxCanFdDriver()
    try:
        fake_cx.getnum_ret = -1
        f = d.recv(timeout_s=0.3)
        assert f is None
        assert d.rx_error is None  # 接收线程未崩
    finally:
        d.close()


# ---------- make_driver 路由 ----------

def test_make_driver_routes_cx(fake_cx):
    import DiagnosticTester
    from can_driver import CxCanFdDriver
    cfg = {"can": {"device": "CX-USBCANFD", "channel": 1, "fd": True,
                   "baudrate": 500000, "data_baudrate": 2000000}}
    d = DiagnosticTester.make_driver(cfg)
    try:
        assert isinstance(d, CxCanFdDriver)
        # 通道与波特率已透传到 SetAbitBaud
        assert ("SetAbitBaud", (0x1111, 1, 500000)) in fake_cx.calls
        assert ("SetDbitBaud", (0x1111, 1, 2000000)) in fake_cx.calls
    finally:
        d.close()


def test_make_driver_unknown_device_raises():
    import DiagnosticTester
    cfg = {"can": {"device": "nope", "channel": 0, "fd": True,
                   "baudrate": 500000}}
    with pytest.raises(RuntimeError,
                       match=r"不支持的设备 nope，可选：mock、.*CX-USBCANFD"):
        DiagnosticTester.make_driver(cfg)
