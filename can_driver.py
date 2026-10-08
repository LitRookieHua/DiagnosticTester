"""帧抽象 + CAN 卡驱动封装（周立功/创芯，测试替身见 tests/mock_driver.py）。"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
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


class ZlgCanDriver(CanDriver):
    _DEV_TYPE_CONSTS = {"USBCANFD-100U": ("ZCAN_USBCANFD_100U", 42),
                        "USBCANFD-200U": ("ZCAN_USBCANFD_200U", 41),
                        "USBCANFD-800U": ("ZCAN_USBCANFD_800U", 59)}

    def __init__(self, device="USBCANFD-200U", device_index=0, channel=0,
                 baudrate=500000, data_baudrate=2000000, fd=True):
        super().__init__()
        zmod = import_zlgcan()
        self._zmod = zmod
        self._z = zmod.ZCAN()
        self.channel = channel
        self.fd = fd
        self.rx_error = None
        if device not in self._DEV_TYPE_CONSTS:
            raise RuntimeError(f"不支持的设备 {device}，可选 {list(self._DEV_TYPE_CONSTS)}")
        const_name, fallback = self._DEV_TYPE_CONSTS[device]
        dev_type = getattr(zmod, const_name, fallback)
        self._dev = self._z.OpenDevice(dev_type, device_index, 0)
        if not self._dev:
            raise RuntimeError(f"打开 {device}#{device_index} 失败：检查 USB 连接与设备索引")
        try:
            # USBCANFD-200U 仅在 CANFD 通道模式下能 StartCAN；经典 CAN 帧与 FD 帧
            # 都走同一 CANFD 通道（仲裁域波特率 baudrate，数据域波特率 data_baudrate）
            self._set_value(f"{channel}/canfd_abit_baud_rate", str(baudrate))
            self._set_value(f"{channel}/canfd_dbit_baud_rate", str(data_baudrate))
            self._set_value(f"{channel}/initenal_resistance", "1")
            self._set_value(f"{channel}/set_device_tx_echo", "0")
            cfg = zmod.ZCAN_CHANNEL_INIT_CONFIG()
            cfg.can_type = zmod.ZCAN_TYPE_CANFD
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

    def _set_value(self, key: str, value: str):
        # ZCAN_SetValue 第三个参数是 c_void_p，必须显式补 \0 结尾，
        # 否则 C 层读到未初始化字节，波特率/电阻等配置被污染，StartCAN 失败
        if self._z.ZCAN_SetValue(self._dev, key, value.encode("utf-8") + b"\x00") != 1:
            raise RuntimeError(f"设置 {key}={value} 失败")

    def _rx_loop(self):
        BATCH = 100
        z, zmod = self._z, self._zmod
        try:
            while self._running.is_set():
                if self.fd:
                    # FD 通道上经典帧与 FD 帧分别落在两个接收队列，都要取走
                    n = z.GetReceiveNum(self._chn, zmod.ZCAN_TYPE_CANFD)
                    if n:
                        msgs, cnt = z.ReceiveFD(self._chn, min(n, BATCH), 100)
                        for m in msgs[:cnt]:
                            f = m.frame
                            self.rx_queue.put(CanFrame(
                                f.can_id, bytes(f.data)[:f.len], is_fd=True,
                                bitrate_switch=bool(f.flags & 0x1)))
                n = z.GetReceiveNum(self._chn, zmod.ZCAN_TYPE_CAN)
                if n:
                    msgs, cnt = z.Receive(self._chn, min(n, BATCH), 100)
                    for m in msgs[:cnt]:
                        f = m.frame
                        self.rx_queue.put(CanFrame(
                            f.can_id, bytes(f.data)[:f.can_dlc], is_fd=False))
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
        z, zmod = self._z, self._zmod
        with self._tx_lock:
            if frame.is_fd:
                msgs = (zmod.ZCAN_TransmitFD_Data * 1)()
                m = msgs[0]
                f = m.frame
                # 实测 ECU CanIf 只接受 8 字节 FD 帧（64 字节配置与固件实际不符），
                # 故最小补齐到 8；更长载荷按 DLC 阶梯补齐
                wire_len = max(8, dlc_to_len(len_to_dlc(len(frame.data))))
                data = frame.data + bytes(wire_len - len(frame.data))  # 补齐到合法长度，避免未初始化字节上线
                f.can_id = frame.can_id
                f.len = wire_len  # 结构体 len 为字节数（0-64），固件自行转 DLC
                f.flags = 0x1 if frame.bitrate_switch else 0x0  # bit0 BRS 加速
                m.transmit_type = 0
                for i, b in enumerate(data):
                    f.data[i] = b
                if z.TransmitFD(self._chn, msgs, 1) != 1:
                    raise RuntimeError("CAN FD 发送失败")
            else:
                msgs = (zmod.ZCAN_Transmit_Data * 1)()
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
                    if n > 0:
                        cnt = min(n, BATCH)
                        msgs = (cx.ZCAN_ReceiveFD_Data * cnt)()
                        num = z.ReceiveFD(self._chn, msgs, cnt, 0)
                        for m in msgs[:max(num, 0)]:
                            f = m.frame
                            self.rx_queue.put(CanFrame(
                                f.can_id, bytes(f.data)[:f.len], is_fd=True,
                                bitrate_switch=bool(f.brs)))
                n = z.GetReceiveNum(self._chn, cx.TYPE_CAN)
                if n > 0:
                    cnt = min(n, BATCH)
                    msgs = (cx.ZCAN_Receive_Data * cnt)()
                    num = z.Receive(self._chn, msgs, cnt, 0)
                    for m in msgs[:max(num, 0)]:
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
