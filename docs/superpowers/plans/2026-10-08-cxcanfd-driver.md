# 创芯 CANFD 卡适配（CxCanFdDriver）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** diag 工具新增创芯双通道 USB-CANFD 卡支持，与 ZLG 双后端共存，按 `can.device` 名路由。

**Architecture:** 镜像 zlgcan 的 vendor 分层——`vendor/cxcanfd/` 放 DLL + 自写 ctypes 绑定模块 `cxcanfd.py`（创芯无官方 Python 包装）；`can_driver.py` 新增 `CxCanFdDriver(CanDriver)`，收发行为对齐 `ZlgCanDriver`；`diag.py` 的 `make_driver()` 按设备名三路由。上层（isotp/flow/uds）只依赖 `CanDriver` 接口，零改动。

**Tech Stack:** Python 3.8+（64 位）、ctypes（标准库）、pytest。设计文档：`docs/superpowers/specs/2026-10-08-cxcanfd-driver-design.md`。

**背景速览（给零上下文的执行者）：**
- `can_driver.py`：`CanFrame` 数据类、`CanDriver` 基类（`send/recv/close`，`recv` 从 `rx_queue` 取帧）、`ZlgCanDriver` 现有实现、`FD_DLC_LEN/dlc_to_len/len_to_dlc` 工具函数
- 创芯官方示例：`spec/python(x64)_example_python3.8.8_v2.0/cxcanfd_x64_v2.0.py`（裸 ctypes，API 是 ZLG `ZCAN_*` 族的克隆）
- 创芯与 ZLG 的关键差异：波特率用 `ZCAN_SetAbitBaud/ZCAN_SetDbitBaud` 专用函数（ZLG 用字符串键 `ZCAN_SetValue`）；新增 `ZCAN_SetCANFDStandard`（0=ISO）；无终端电阻/回显软件开关；句柄为 `c_void_p`
- ECU quirk（与卡无关，必须保留）：FD 帧发送时载荷最小补齐到 8 字节、更长按 DLC 阶梯（8→12→16→20→24→32→48→64）
- 测试基建：`tests/conftest.py` 把仓库根加入 `sys.path`，测试直接 `import can_driver`

---

### Task 1: 组装 vendor/cxcanfd 目录（DLL + README）

**Files:**
- Create: `vendor/cxcanfd/ControlCANFD.dll`（二进制复制）
- Create: `vendor/cxcanfd/README.md`

- [ ] **Step 1: 复制 DLL**

```bash
mkdir -p vendor/cxcanfd
cp "spec/python(x64)_example_python3.8.8_v2.0/ControlCANFD.dll" vendor/cxcanfd/
```

- [ ] **Step 2: 确认 Python 是 64 位**（DLL 位数必须匹配）

Run: `python -c "import struct; print(struct.calcsize('P')*8)"`
Expected: `64`（若是 32，停止并报告——需向厂家索取 x86 DLL，本计划其余任务不受影响但冒烟测试会挂）

- [ ] **Step 3: 写 vendor/cxcanfd/README.md**

```markdown
# 创芯 ControlCANFD SDK 放置目录

来源：创芯科技官方 python 示例包 `python(x64)_example_python3.8.8_v2.0`
（本仓库 `spec/` 下有备份）。

| 文件 | 说明 |
| --- | --- |
| `ControlCANFD.dll` | 64 位 DLL（Python 须为 64 位；32 位 Python 需向厂家索取 x86 版） |
| `cxcanfd.py` | 自写 ctypes 绑定（官方无 Python 包装），DLL 按 `__file__` 所在目录加载 |

设备类型：`VCI_USBCAN2 = 41`（双通道 USB-CANFD，配置里设备名 `CX-USBCANFD`）。

注意：终端电阻无软件开关（ZLG 是软件开启 `initenal_resistance`），
需按卡上拨码/跳线或外接电阻手动处理。
```

- [ ] **Step 4: 验证与提交**

Run: `ls vendor/cxcanfd/` → `ControlCANFD.dll  README.md`

```bash
git add vendor/cxcanfd/ControlCANFD.dll vendor/cxcanfd/README.md
git commit -m "feat(driver): 创芯 vendor 目录组装（DLL + README）"
```

---

### Task 2: cxcanfd.py ctypes 绑定模块（TDD）

**Files:**
- Create: `vendor/cxcanfd/cxcanfd.py`
- Test: `tests/test_cxcanfd_binding.py`

- [ ] **Step 1: 写失败的冒烟测试** `tests/test_cxcanfd_binding.py`

```python
"""vendor/cxcanfd 绑定冒烟：DLL 能加载、常量/结构体齐全（不碰硬件）。"""


def test_import_and_constants():
    from can_driver import import_cxcanfd
    cx = import_cxcanfd()
    assert cx.VCI_USBCAN2 == 41
    assert cx.TYPE_CAN == 0
    assert cx.TYPE_CANFD == 1
    assert cx.ZCAN() is not None


def test_struct_shapes():
    from can_driver import import_cxcanfd
    cx = import_cxcanfd()
    cfg = cx.ZCAN_CHANNEL_INIT_CONFIG()
    cfg.can_type = cx.TYPE_CANFD
    cfg.config.canfd.mode = 0
    assert cfg.can_type == 1
    msgs = (cx.ZCAN_ReceiveFD_Data * 2)()
    msgs[0].frame.can_id = 0x78C
    msgs[0].frame.len = 8
    msgs[0].frame.data[0] = 0x62
    assert msgs[0].frame.data[0] == 0x62
    assert len(msgs[0].frame.data) == 64
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_cxcanfd_binding.py -v`
Expected: FAIL — `AttributeError: ... does not have the attribute 'import_cxcanfd'`（`import_cxcanfd` 尚不存在）

- [ ] **Step 3: 写绑定模块** `vendor/cxcanfd/cxcanfd.py`

结构体与函数原型逐字对照官方示例 `cxcanfd_x64_v2.0.py`（仅去掉未使用的 `VCI_INIT_CONFIG`/`VCI_CAN_OBJ`，滤波函数本工具不用也不封装）：

```python
"""创芯 ControlCANFD.dll 的 ctypes 绑定。

创芯无官方 Python 包装（官方示例为裸 ctypes 脚本），本模块自写，
接口风格对齐 vendor/zlgcan/zlgcan.py：结构体 + 常量 + ZCAN 类。

DLL 按本模块所在目录加载（官方示例 './ControlCANFD.dll' 依赖进程 CWD，
换目录运行会失败）。Python 位数须与 DLL 一致（仓库内置 64 位 DLL）。
"""
from ctypes import (Structure, Union, byref, c_long, c_ubyte, c_uint, c_ulong,
                    c_ulonglong, c_ushort, c_void_p, windll)
from pathlib import Path

VCI_USBCAN2 = 41
STATUS_OK = 1
TYPE_CAN = 0
TYPE_CANFD = 1


class _ZCAN_CHANNEL_CAN_INIT_CONFIG(Structure):
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("reserved", c_uint),
                ("filter", c_ubyte),
                ("timing0", c_ubyte),
                ("timing1", c_ubyte),
                ("mode", c_ubyte)]


class _ZCAN_CHANNEL_CANFD_INIT_CONFIG(Structure):
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("abit_timing", c_uint),
                ("dbit_timing", c_uint),
                ("brp", c_uint),
                ("filter", c_ubyte),
                ("mode", c_ubyte),
                ("pad", c_ushort),
                ("reserved", c_uint)]


class _ZCAN_CHANNEL_INIT_CONFIG(Union):
    _fields_ = [("can", _ZCAN_CHANNEL_CAN_INIT_CONFIG),
                ("canfd", _ZCAN_CHANNEL_CANFD_INIT_CONFIG)]


class ZCAN_CHANNEL_INIT_CONFIG(Structure):
    _fields_ = [("can_type", c_uint),
                ("config", _ZCAN_CHANNEL_INIT_CONFIG)]


class ZCAN_CAN_FRAME(Structure):
    _fields_ = [("can_id", c_uint, 29),
                ("err", c_uint, 1),
                ("rtr", c_uint, 1),
                ("eff", c_uint, 1),
                ("can_dlc", c_ubyte),
                ("__pad", c_ubyte),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data", c_ubyte * 8)]


class ZCAN_CANFD_FRAME(Structure):
    _fields_ = [("can_id", c_uint, 29),
                ("err", c_uint, 1),
                ("rtr", c_uint, 1),
                ("eff", c_uint, 1),
                ("len", c_ubyte),
                ("brs", c_ubyte, 1),
                ("esi", c_ubyte, 1),
                ("__res", c_ubyte, 6),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data", c_ubyte * 64)]


class ZCAN_Transmit_Data(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("transmit_type", c_uint)]


class ZCAN_Receive_Data(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("timestamp", c_ulonglong)]


class ZCAN_TransmitFD_Data(Structure):
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("transmit_type", c_uint)]


class ZCAN_ReceiveFD_Data(Structure):
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("timestamp", c_ulonglong)]


_dll = windll.LoadLibrary(str(Path(__file__).resolve().parent / "ControlCANFD.dll"))

_dll.ZCAN_OpenDevice.restype = c_void_p
_dll.ZCAN_SetAbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
_dll.ZCAN_SetDbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
_dll.ZCAN_SetCANFDStandard.argtypes = (c_void_p, c_ulong, c_ulong)
_dll.ZCAN_InitCAN.argtypes = (c_void_p, c_ulong, c_void_p)
_dll.ZCAN_InitCAN.restype = c_void_p
_dll.ZCAN_StartCAN.argtypes = (c_void_p,)
_dll.ZCAN_Transmit.argtypes = (c_void_p, c_void_p, c_ulong)
_dll.ZCAN_TransmitFD.argtypes = (c_void_p, c_void_p, c_ulong)
_dll.ZCAN_GetReceiveNum.argtypes = (c_void_p, c_ulong)
_dll.ZCAN_Receive.argtypes = (c_void_p, c_void_p, c_ulong, c_long)
_dll.ZCAN_ReceiveFD.argtypes = (c_void_p, c_void_p, c_ulong, c_long)
_dll.ZCAN_ResetCAN.argtypes = (c_void_p,)
_dll.ZCAN_CloseDevice.argtypes = (c_void_p,)


class ZCAN:
    def OpenDevice(self, device_type, device_index, reserved=0):
        return _dll.ZCAN_OpenDevice(device_type, device_index, reserved)

    def CloseDevice(self, device_handle):
        return _dll.ZCAN_CloseDevice(device_handle)

    def SetAbitBaud(self, device_handle, channel, baud):
        return _dll.ZCAN_SetAbitBaud(device_handle, channel, baud)

    def SetDbitBaud(self, device_handle, channel, baud):
        return _dll.ZCAN_SetDbitBaud(device_handle, channel, baud)

    def SetCANFDStandard(self, device_handle, channel, standard):
        return _dll.ZCAN_SetCANFDStandard(device_handle, channel, standard)

    def InitCAN(self, device_handle, channel_index, config):
        return _dll.ZCAN_InitCAN(device_handle, channel_index, byref(config))

    def StartCAN(self, channel_handle):
        return _dll.ZCAN_StartCAN(channel_handle)

    def ResetCAN(self, channel_handle):
        return _dll.ZCAN_ResetCAN(channel_handle)

    def GetReceiveNum(self, channel_handle, can_type):
        return _dll.ZCAN_GetReceiveNum(channel_handle, can_type)

    def Transmit(self, channel_handle, msgs, length):
        return _dll.ZCAN_Transmit(channel_handle, byref(msgs), length)

    def TransmitFD(self, channel_handle, msgs, length):
        return _dll.ZCAN_TransmitFD(channel_handle, byref(msgs), length)

    def Receive(self, channel_handle, msgs, length, timeout):
        return _dll.ZCAN_Receive(channel_handle, byref(msgs), length, timeout)

    def ReceiveFD(self, channel_handle, msgs, length, timeout):
        return _dll.ZCAN_ReceiveFD(channel_handle, byref(msgs), length, timeout)
```

并在 `can_driver.py` 的 `import_zlgcan()`（约 56-70 行）之后新增导入函数（先加这个，测试才可能过）：

```python
def import_cxcanfd():
    # 创芯无官方 Python 包，绑定只由 vendor 目录提供（与 zlgcan 的 pip 回退不同）
    vendor = Path(__file__).resolve().parent / "vendor" / "cxcanfd"
    if not (vendor / "cxcanfd.py").exists():
        raise RuntimeError("vendor/cxcanfd 未组装：见 vendor/cxcanfd/README.md")
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    os.environ["PATH"] = str(vendor) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(vendor))
    import cxcanfd
    return cxcanfd
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_cxcanfd_binding.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add vendor/cxcanfd/cxcanfd.py can_driver.py tests/test_cxcanfd_binding.py
git commit -m "feat(driver): 创芯 ControlCANFD ctypes 绑定模块"
```

---

### Task 3: CxCanFdDriver（测试先行，完整实现）

**Files:**
- Modify: `can_driver.py`（文件末尾追加类；模块 docstring 改为「帧抽象 + CAN 卡驱动封装（周立功/创芯，测试替身见 tests/mock_driver.py）」；顶部 import 增加 `import time`）
- Test: `tests/test_cx_driver.py`

测试策略：不依赖真 DLL/硬件——`monkeypatch` 掉 `can_driver.import_cxcanfd`，注入 fake 模块（`types.SimpleNamespace` + 简化 ctypes 结构体 + 记录调用的 `FakeZCAN`）。fake 结构体用普通 ctypes 字段（不做位域），只要字段名/偏移语义满足驱动访问即可。

- [ ] **Step 1: 写失败的测试** `tests/test_cx_driver.py`

```python
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
        return len(self.fd_frames) if can_type == 1 else len(self.can_frames)

    def ReceiveFD(self, chn, msgs, n, timeout):
        spec = self.fd_frames.pop(0)
        f = msgs[0].frame
        f.can_id, f.len, f.brs = spec["id"], spec["len"], spec.get("brs", 0)
        for i, b in enumerate(spec["data"]):
            f.data[i] = b
        return 1

    def Receive(self, chn, msgs, n, timeout):
        spec = self.can_frames.pop(0)
        f = msgs[0].frame
        f.can_id, f.can_dlc = spec["id"], len(spec["data"])
        for i, b in enumerate(spec["data"]):
            f.data[i] = b
        return 1

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_cx_driver.py -v`
Expected: 全部 ERROR/FAIL — `ImportError: cannot import name 'CxCanFdDriver'`

- [ ] **Step 3: 在 can_driver.py 末尾实现 CxCanFdDriver**

同时：模块 docstring 改为「帧抽象 + CAN 卡驱动封装（周立功/创芯，测试替身见 tests/mock_driver.py）」，顶部加 `import time`。

```python
class CxCanFdDriver(CanDriver):
    """创芯双通道 USB-CANFD（ControlCANFD.dll，VCI_USBCAN2=41）。"""

    _DEV_TYPE_CONSTS = {"CX-USBCANFD": ("VCI_USBCAN2", 41)}

    def __init__(self, device="CX-USBCANFD", device_index=0, channel=0,
                 baudrate=500000, data_baudrate=2000000, fd=True):
        super().__init__()
        cx = import_cxcanfd()
        self._cx = cx
        self._z = cx.ZCAN()
        self.channel = channel
        self.fd = fd
        self.rx_error = None
        if device not in self._DEV_TYPE_CONSTS:
            raise RuntimeError(f"不支持的设备 {device}，可选 {list(self._DEV_TYPE_CONSTS)}")
        const_name, fallback = self._DEV_TYPE_CONSTS[device]
        dev_type = getattr(cx, const_name, fallback)
        self._dev = self._z.OpenDevice(dev_type, device_index, 0)
        if not self._dev:
            raise RuntimeError(f"打开 {device}#{device_index} 失败：检查 USB 连接与设备索引")
        try:
            # 波特率用专用函数设置（ZLG 走 ZCAN_SetValue 字符串键，创芯没有）
            if self._z.SetAbitBaud(self._dev, channel, baudrate) != 1:
                raise RuntimeError(f"设置通道 {channel} 仲裁域波特率 {baudrate} 失败")
            if self._z.SetDbitBaud(self._dev, channel, data_baudrate) != 1:
                raise RuntimeError(f"设置通道 {channel} 数据域波特率 {data_baudrate} 失败")
            if self._z.SetCANFDStandard(self._dev, channel, 0) != 1:  # 0=ISO
                raise RuntimeError(f"设置通道 {channel} ISO 模式失败")
            cfg = cx.ZCAN_CHANNEL_INIT_CONFIG()
            cfg.can_type = cx.TYPE_CANFD
            cfg.config.canfd.mode = 0  # 0-正常模式 1-只听模式
            self._chn = self._z.InitCAN(self._dev, channel, cfg)
            if not self._chn:
                raise RuntimeError(f"通道 {channel} 初始化失败")
            if self._z.StartCAN(self._chn) != 1:
                raise RuntimeError(f"通道 {channel} 启动失败")
        except Exception:
            self._z.CloseDevice(self._dev)
            raise
        self._running = threading.Event()
        self._running.set()
        self._tx_lock = threading.Lock()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def _rx_loop(self):
        BATCH = 100
        z, cx = self._z, self._cx
        try:
            while self._running.is_set():
                if self.fd:
                    # FD 通道上经典帧与 FD 帧分别落在两个接收队列，都要取走
                    n = z.GetReceiveNum(self._chn, cx.TYPE_CANFD)
                    if n:
                        cnt = min(n, BATCH)
                        msgs = (cx.ZCAN_ReceiveFD_Data * cnt)()
                        num = z.ReceiveFD(self._chn, msgs, cnt, 0)
                        for m in msgs[:num]:
                            f = m.frame
                            self.rx_queue.put(CanFrame(
                                f.can_id, bytes(f.data)[:f.len], is_fd=True,
                                bitrate_switch=bool(f.brs)))
                n = z.GetReceiveNum(self._chn, cx.TYPE_CAN)
                if n:
                    cnt = min(n, BATCH)
                    msgs = (cx.ZCAN_Receive_Data * cnt)()
                    num = z.Receive(self._chn, msgs, cnt, 0)
                    for m in msgs[:num]:
                        f = m.frame
                        self.rx_queue.put(CanFrame(
                            f.can_id, bytes(f.data)[:f.can_dlc], is_fd=False))
                time.sleep(0.01)  # 官方示例节拍，避免 CPU 空转
        except Exception as e:
            self.rx_error = e
            print(f"CAN 接收线程异常退出：{e}", file=sys.stderr)

    def send(self, frame: CanFrame):
        if frame.is_fd != self.fd:
            driver_mode = "CAN FD" if self.fd else "经典 CAN"
            frame_mode = "CAN FD" if frame.is_fd else "经典 CAN"
            raise ValueError(f"帧模式不匹配：驱动工作在 {driver_mode} 模式，收到 {frame_mode} 帧")
        if not frame.is_fd and len(frame.data) > 8:
            raise ValueError("经典 CAN 帧最长 8 字节")
        z, cx = self._z, self._cx
        with self._tx_lock:
            if frame.is_fd:
                msgs = (cx.ZCAN_TransmitFD_Data * 1)()
                m = msgs[0]
                f = m.frame
                # 同 ZLG：实测 ECU CanIf 只接受 8 字节 FD 帧，最小补 8、更长按 DLC 阶梯补齐
                wire_len = max(8, dlc_to_len(len_to_dlc(len(frame.data))))
                data = frame.data + bytes(wire_len - len(frame.data))
                f.can_id = frame.can_id
                f.len = wire_len  # 结构体 len 为字节数（0-64），固件自行转 DLC
                f.brs = 1 if frame.bitrate_switch else 0
                m.transmit_type = 0
                for i, b in enumerate(data):
                    f.data[i] = b
                if z.TransmitFD(self._chn, msgs, 1) != 1:
                    raise RuntimeError("CAN FD 发送失败")
            else:
                msgs = (cx.ZCAN_Transmit_Data * 1)()
                m = msgs[0]
                f = m.frame
                f.can_id = frame.can_id
                f.can_dlc = len(frame.data)
                m.transmit_type = 0
                for i, b in enumerate(frame.data):
                    f.data[i] = b
                if z.Transmit(self._chn, msgs, 1) != 1:
                    raise RuntimeError("CAN 发送失败")

    def close(self):
        self._running.clear()
        self._rx_thread.join(timeout=2.0)
        try:
            self._z.ResetCAN(self._chn)
            self._z.CloseDevice(self._dev)
        except Exception:
            pass
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_cx_driver.py -v`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add can_driver.py tests/test_cx_driver.py
git commit -m "feat(driver): 新增创芯 CxCanFdDriver（收发行为对齐 ZLG）"
```

---

### Task 4: make_driver 三路由（测试先行）

**Files:**
- Modify: `diag.py:30-42`（`make_driver` 函数整体替换）
- Test: `tests/test_cx_driver.py`（追加路由测试）

- [ ] **Step 1: 追加失败的测试**（`tests/test_cx_driver.py` 末尾）

```python
# ---------- make_driver 路由 ----------

def test_make_driver_routes_cx(fake_cx):
    import diag
    from can_driver import CxCanFdDriver
    cfg = {"can": {"device": "CX-USBCANFD", "channel": 1, "fd": True,
                   "baudrate": 500000, "data_baudrate": 2000000}}
    d = diag.make_driver(cfg)
    try:
        assert isinstance(d, CxCanFdDriver)
        # 通道与波特率已透传到 SetAbitBaud
        assert ("SetAbitBaud", (0x1111, 1, 500000)) in fake_cx.calls
    finally:
        d.close()


def test_make_driver_unknown_device_raises():
    import diag
    cfg = {"can": {"device": "nope", "channel": 0, "fd": True,
                   "baudrate": 500000}}
    with pytest.raises(RuntimeError, match="不支持的设备"):
        diag.make_driver(cfg)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_cx_driver.py -v -k make_driver`
Expected: `test_make_driver_routes_cx` FAIL（现在未知设备会被当成 ZLG 设备名传入 ZlgCanDriver）；`test_make_driver_unknown_device_raises` FAIL（ZlgCanDriver 报错文案是「不支持的设备 nope，可选 [...]」——若文案恰好匹配则此条先过，以 routes_cx 失败为准）

- [ ] **Step 3: 替换 diag.py 的 make_driver**（`diag.py:30-42`）

```python
def make_driver(cfg):
    can_cfg = cfg["can"]
    device = can_cfg["device"]
    if device == "mock":
        tests_dir = str(TOOLS_DIR / "tests")
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from mock_driver import MockCanDriver
        return MockCanDriver()
    from can_driver import CxCanFdDriver, ZlgCanDriver
    common = dict(channel=can_cfg["channel"], baudrate=can_cfg["baudrate"],
                  data_baudrate=can_cfg.get("data_baudrate", 2000000),
                  fd=can_cfg["fd"])
    if device in ZlgCanDriver._DEV_TYPE_CONSTS:
        return ZlgCanDriver(device=device, **common)
    if device in CxCanFdDriver._DEV_TYPE_CONSTS:
        return CxCanFdDriver(device=device, **common)
    raise RuntimeError(
        f"不支持的设备 {device}，可选：mock、"
        f"{'、'.join((*ZlgCanDriver._DEV_TYPE_CONSTS, *CxCanFdDriver._DEV_TYPE_CONSTS))}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_cx_driver.py -v`
Expected: 15 passed

- [ ] **Step 5: 提交**

```bash
git add diag.py tests/test_cx_driver.py
git commit -m "feat(driver): make_driver 按 can.device 路由 mock/ZLG/创芯"
```

---

### Task 5: 示例配置 + README + 全量回归

**Files:**
- Create: `configs/example_cx.yaml`
- Modify: `README.md`（四处小改）

- [ ] **Step 1: 写 configs/example_cx.yaml**

```yaml
can:
  device: CX-USBCANFD
  channel: 0
  fd: true
  baudrate: 500000
  data_baudrate: 2000000

diagnostic:
  request_id: 0x70C
  response_id: 0x78C
  functional_id: 0x7DF

timing:
  p2_ms: 50
  p2star_ms: 5000
  step_delay_ms: 100
```

- [ ] **Step 2: 改 README.md**（保持现有行号风格，四处）

1) 第 3 行开头：

```markdown
# 诊断测试工具（diag）

周立功 / 创芯 USB-CANFD 卡上的 UDS 诊断测试工具。经典 CAN / CAN FD 均支持，
```

2) 「快速上手」前提段（原 11-13 行）之后追加小节：

```markdown
### 创芯卡（CX-USBCANFD）

配置 `can.device: CX-USBCANFD` 即用创芯双通道卡：DLL 已随仓库内置
（`vendor/cxcanfd/`，64 位，Python 须 64 位），无需另外组装 SDK。
终端电阻无软件开关，请确认卡上拨码/跳线或外接电阻。
```

3) 「配置文件」can 节（原 50-52 行）device 描述改为：

```markdown
- **can** — 总线接入：`device`（周立功设备名 `USBCANFD-100U/200U/800U`、
  创芯 `CX-USBCANFD`，`mock` 走内置 Mock 驱动、免硬件）、`channel` 通道号、
  `fd` 是否 CAN FD、`baudrate` 仲裁域波特率；
  `data_baudrate` 为 FD 数据域波特率（可省略，默认 2000000）。
```

4) 「常见问题」第一条（原 205-206 行）补充创芯一句：

```markdown
- **`打开 USBCANFD-200U#0 失败` / `打开 CX-USBCANFD#0 失败`** — 检查 USB 连接
  与设备索引；确认没有其他程序（zcanpro、创芯官方 demo 等）占用设备。
```

- [ ] **Step 3: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 全部 passed（原有用例 + 新增 test_cxcanfd_binding 2 条 + test_cx_driver 14 条）

- [ ] **Step 4: 提交**

```bash
git add configs/example_cx.yaml README.md
git commit -m "feat(driver): 创芯示例配置与 README 双卡说明"
```

---

## 真机验证（人工，需硬件）

计划内代码全部落地后，由持卡同学执行：

1. 创芯卡插 USB、CAN_H/L 接 ECU，确认终端电阻
2. `python diag.py probe --config configs/example_cx.yaml 22F195` → 期望打印响应（如 `62F195...`）
3. `python diag.py chat --config configs/example_cx.yaml` 跑 `1003` → `22F190` 冒烟
4. 任一命令即可在 `logs/example_cx_*` 下核对 `trace.blf` 落盘

失败排查：打不开→占用/索引；发送失败→物理层（参考 README 常见问题）。
