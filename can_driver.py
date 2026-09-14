"""帧抽象 + 周立功 CAN 卡驱动封装（测试替身见 tests/mock_driver.py）。"""
from __future__ import annotations

import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

FD_DLC_LEN = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


def dlc_to_len(dlc: int) -> int:
    return FD_DLC_LEN[dlc & 0x0F]


def len_to_dlc(n: int) -> int:
    if n <= 8:
        return n
    for d, l in enumerate(FD_DLC_LEN):
        if l >= n:
            return d
    raise ValueError(f"非法帧长 {n}")


def _get(obj, *names):
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(f"结构体缺少字段 {names}")


def _set(obj, value, *names):
    for n in names:
        if hasattr(obj, n):
            setattr(obj, n, value)
            return
    raise AttributeError(f"结构体缺少字段 {names}")


@dataclass
class CanFrame:
    can_id: int
    data: bytes
    is_fd: bool = False
    bitrate_switch: bool = False


class CanDriver:
    """帧级驱动接口：send 同步发送，recv 阻塞取帧（无帧返回 None）。"""

    def __init__(self):
        self.rx_queue: queue.Queue = queue.Queue()

    def send(self, frame: CanFrame) -> None:
        raise NotImplementedError

    def recv(self, timeout_s: float):
        try:
            return self.rx_queue.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def close(self) -> None:
        pass


def import_zlgcan():
    try:
        import zlgcan
        return zlgcan
    except ImportError:
        vendor = Path(__file__).resolve().parent / "vendor" / "zlgcan"
        if (vendor / "zlgcan.py").exists():
            os.environ["PATH"] = str(vendor) + os.pathsep + os.environ.get("PATH", "")
            sys.path.insert(0, str(vendor))
            import zlgcan
            return zlgcan
        raise


class ZlgCanDriver(CanDriver):
    DEVICE_TYPES = {"USBCANFD-100U": 58, "USBCANFD-200U": 59, "USBCANFD-800U": 60}

    def __init__(self, device="USBCANFD-200U", device_index=0, channel=0,
                 baudrate=500000, data_baudrate=2000000, fd=True):
        super().__init__()
        z = import_zlgcan()
        self._z = z
        self.channel = channel
        self.fd = fd
        if device not in self.DEVICE_TYPES:
            raise RuntimeError(f"不支持的设备 {device}，可选 {list(self.DEVICE_TYPES)}")
        self._dev = z.ZCAN_OpenDevice(self.DEVICE_TYPES[device], device_index, 0)
        if not self._dev:
            raise RuntimeError(f"打开 {device}#{device_index} 失败：检查 USB 连接与设备索引")
        ch = str(channel).encode()
        try:
            if fd:
                z.ZCAN_SetValue(self._dev, ch + b"/canfd_abit_baud_rate", str(baudrate).encode())
                z.ZCAN_SetValue(self._dev, ch + b"/canfd_dbit_baud_rate", str(data_baudrate).encode())
            else:
                z.ZCAN_SetValue(self._dev, ch + b"/baud_rate", str(baudrate).encode())
            if z.ZCAN_InitCAN(self._dev, channel, z.ZCAN_CHANNEL_MODE_NORMAL) != 1:
                raise RuntimeError(f"通道 {channel} 初始化失败")
        except Exception:
            z.ZCAN_CloseDevice(self._dev)
            raise
        self._running = threading.Event()
        self._running.set()
        self._tx_lock = threading.Lock()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def _rx_loop(self):
        BATCH = 100
        z = self._z
        if self.fd:
            buf = (z.ZCAN_ReceiveFD_Data * BATCH)()
            while self._running.is_set():
                n = z.ZCAN_ReceiveFD(self._dev, self.channel, buf, BATCH, 100)
                for i in range(n):
                    m = buf[i]
                    data = bytes(_get(m, "Data", "data"))[:dlc_to_len(_get(m, "DLC", "dlc"))]
                    self.rx_queue.put(CanFrame(_get(m, "ID", "id"), data, is_fd=True,
                                               bitrate_switch=bool(_get(m, "BRS", "brs"))))
        else:
            buf = (z.ZCAN_Receive_Data * BATCH)()
            while self._running.is_set():
                n = z.ZCAN_Receive(self._dev, self.channel, buf, BATCH, 100)
                for i in range(n):
                    m = buf[i]
                    length = _get(m, "DataLen", "data_len", "dlc", "len") & 0xFF
                    data = bytes(_get(m, "Data", "data"))[:length]
                    self.rx_queue.put(CanFrame(_get(m, "ID", "id"), data, is_fd=False))

    def send(self, frame: CanFrame):
        z = self._z
        with self._tx_lock:
            if frame.is_fd:
                msgs = (z.ZCAN_TransmitFD_Data * 1)()
                m = msgs[0]
                _set(m, 0, "SendType", "send_type")
                _set(m, 1 if frame.bitrate_switch else 0, "BRS", "brs")
                _set(m, len_to_dlc(len(frame.data)), "DLC", "dlc")
                _set(m, frame.can_id, "ID", "id")
                arr = _get(m, "Data", "data")
                for i, b in enumerate(frame.data):
                    arr[i] = b
                if z.ZCAN_TransmitFD(self._dev, self.channel, msgs, 1) != 1:
                    raise RuntimeError("CAN FD 发送失败")
            else:
                msgs = (z.ZCAN_Transmit_Data * 1)()
                m = msgs[0]
                _set(m, 0, "SendType", "send_type")
                _set(m, len(frame.data), "DataLen", "data_len", "dlc", "len")
                _set(m, frame.can_id, "ID", "id")
                arr = _get(m, "Data", "data")
                for i, b in enumerate(frame.data):
                    arr[i] = b
                if z.ZCAN_Transmit(self._dev, self.channel, msgs, 1) != 1:
                    raise RuntimeError("CAN 发送失败")

    def close(self):
        self._running.clear()
        try:
            self._z.ZCAN_ResetCAN(self._dev, self.channel)
            self._z.ZCAN_CloseDevice(self._dev)
        except Exception:
            pass
