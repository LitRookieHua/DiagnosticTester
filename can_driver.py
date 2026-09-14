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
    if n < 0:
        raise ValueError(f"非法帧长 {n}")
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
            if str(vendor) not in sys.path:
                sys.path.insert(0, str(vendor))
            os.environ["PATH"] = str(vendor) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(vendor))
            import zlgcan
            return zlgcan
        raise


class ZlgCanDriver(CanDriver):
    _DEV_TYPE_CONSTS = {"USBCANFD-100U": ("ZCAN_USBCANFD_100U", 42),
                        "USBCANFD-200U": ("ZCAN_USBCANFD_200U", 41),
                        "USBCANFD-800U": ("ZCAN_USBCANFD_800U", 59)}

    def __init__(self, device="USBCANFD-200U", device_index=0, channel=0,
                 baudrate=500000, data_baudrate=2000000, fd=True):
        super().__init__()
        z = import_zlgcan()
        self._z = z
        self.channel = channel
        self.fd = fd
        self.rx_error = None
        if device not in self._DEV_TYPE_CONSTS:
            raise RuntimeError(f"不支持的设备 {device}，可选 {list(self._DEV_TYPE_CONSTS)}")
        const_name, fallback = self._DEV_TYPE_CONSTS[device]
        dev_type = getattr(z, const_name, fallback)
        self._dev = z.ZCAN_OpenDevice(dev_type, device_index, 0)
        if not self._dev:
            raise RuntimeError(f"打开 {device}#{device_index} 失败：检查 USB 连接与设备索引")
        try:
            if fd:
                key = f"{channel}/canfd_abit_baud_rate"
                if z.ZCAN_SetValue(self._dev, key.encode(), str(baudrate).encode()) != 1:
                    raise RuntimeError(f"设置波特率失败（{key}）")
                key = f"{channel}/canfd_dbit_baud_rate"
                if z.ZCAN_SetValue(self._dev, key.encode(), str(data_baudrate).encode()) != 1:
                    raise RuntimeError(f"设置波特率失败（{key}）")
            else:
                key = f"{channel}/baud_rate"
                if z.ZCAN_SetValue(self._dev, key.encode(), str(baudrate).encode()) != 1:
                    raise RuntimeError(f"设置波特率失败（{key}）")
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
        try:
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
        z = self._z
        with self._tx_lock:
            if frame.is_fd:
                msgs = (z.ZCAN_TransmitFD_Data * 1)()
                m = msgs[0]
                dlc = len_to_dlc(len(frame.data))
                wire_len = dlc_to_len(dlc)
                data = frame.data + bytes(wire_len - len(frame.data))  # 补齐到 DLC 合法长度，避免未初始化字节上线
                _set(m, 0, "SendType", "send_type")
                _set(m, 1 if frame.bitrate_switch else 0, "BRS", "brs")
                _set(m, dlc, "DLC", "dlc")
                _set(m, frame.can_id, "ID", "id")
                arr = _get(m, "Data", "data")
                for i, b in enumerate(data):
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
        self._rx_thread.join(timeout=2.0)
        try:
            self._z.ZCAN_ResetCAN(self._dev, self.channel)
            self._z.ZCAN_CloseDevice(self._dev)
        except Exception:
            pass
