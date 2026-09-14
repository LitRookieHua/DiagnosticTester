"""ISO-TP 15765-2 传输层：经典 CAN 与 CAN FD 收发。"""
from __future__ import annotations

import queue
import threading
import time

from can_driver import CanDriver, CanFrame

SF, FF, CF, FC = 0x0, 0x1, 0x2, 0x3


class IsoTpError(Exception):
    pass


def encode(payload: bytes, is_fd: bool) -> list[bytes]:
    """字节流 → PCI 帧 data 列表（不含 CAN ID）。

    ≤7 字节：两种模式均用经典 1 字节 SF PCI；FD 下 8-62 字节用 2 字节转义形式；
    CF 一律 1 字节 PCI + 4 位序号（FD 每帧 63 字节、经典每帧 7 字节）。
    """
    if not payload:
        raise ValueError("payload 不能为空")
    if len(payload) > 0xFFF:
        raise ValueError("payload 超过 4095 字节未支持")
    if len(payload) <= 7:
        return [bytes([len(payload)]) + payload]
    if is_fd and len(payload) <= 62:
        return [bytes([0x00, len(payload)]) + payload]
    head = 0x1000 | len(payload)
    if is_fd:
        frames = [bytes([(head >> 8) & 0xFF, head & 0xFF]) + payload[:62]]
        rest, cf_len = payload[62:], 63
    else:
        frames = [bytes([(head >> 8) & 0xFF, head & 0xFF]) + payload[:6]]
        rest, cf_len = payload[6:], 7
    seq = 1
    while rest:
        chunk, rest = rest[:cf_len], rest[cf_len:]
        frames.append(bytes([0x20 | (seq & 0x0F)]) + chunk)
        seq = (seq + 1) & 0x0F
    return frames


class RxAssembler:
    """SF/FF/CF 重组：feed 每次给一帧 data，完整时返回 payload，否则 None。"""

    def __init__(self, is_fd: bool):
        self.is_fd = is_fd
        self.reset()

    def reset(self):
        self.buf = bytearray()
        self.total = None
        self.next_seq = None
        self.last_ts = None

    def expired(self, now: float, n_cr_s: float) -> bool:
        return (self.total is not None and self.last_ts is not None
                and now - self.last_ts > n_cr_s)

    def feed(self, data: bytes, now: float):
        if not data:
            return None
        pci = data[0] >> 4
        if pci == SF:
            self.reset()  # SF 中断重组：清残留状态，防止后续游离 CF 拼出错误报文
            if self.is_fd and data[0] == 0x00:  # 2 字节转义形式（FD 下 8-62 字节）
                if len(data) < 2:
                    return None  # 畸形帧守卫
                ln = data[1]
                return bytes(data[2:2 + ln])
            ln = data[0] & 0x0F  # 经典 1 字节 PCI（≤7 字节）
            return bytes(data[1:1 + ln])
        if pci == FF:
            if len(data) < 2:
                return None  # 畸形帧守卫
            self.total = ((data[0] & 0x0F) << 8) | data[1]
            self.buf = bytearray(data[2:] if self.is_fd else data[2:8])
            self.next_seq = 1
            self.last_ts = now
            return None
        if pci == CF:
            if self.total is None:
                return None  # 无首帧，丢弃
            seq = data[0] & 0x0F  # CF 一律 1 字节 PCI + 4 位序号（FD 每帧 63 字节）
            chunk = data[1:]
            if seq != self.next_seq:
                self.reset()  # 丢序，整个报文作废
                return None
            self.buf += chunk
            self.next_seq = (seq + 1) & 0x0F
            self.last_ts = now
            if len(self.buf) >= self.total:
                payload = bytes(self.buf[:self.total])
                self.reset()
                return payload
            return None
        return None


def _decode_stmin(v: int) -> float:
    """FC 中 STmin 字节 → 秒。0x00-0x7F 毫秒；0xF1-0xF9 为 100-900us。"""
    if v <= 0x7F:
        return v / 1000.0
    if 0xF1 <= v <= 0xF9:
        return (v - 0xF0) / 10000.0
    return 0.0


class IsoTpTransport:
    """一条诊断通道（tx_id/rx_id 对）上的 ISO-TP 传输。

    tap(frame, is_rx)：所有经过本通道的帧回调（BLF 记录用）。
    log(msg)：事件文本日志回调。
    """

    def __init__(self, driver: CanDriver, tx_id: int, rx_id: int, is_fd: bool,
                 fc_stmin_ms: int = 0, fc_bs: int = 0,
                 n_bs_s: float = 1.0, n_cr_s: float = 1.0,
                 tap=None, log=None):
        self.driver = driver
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.is_fd = is_fd
        self.fc_stmin_ms = fc_stmin_ms
        self.fc_bs = fc_bs
        self.n_bs_s = n_bs_s
        self.n_cr_s = n_cr_s
        self.tap = tap
        self.log = log or (lambda *a, **k: None)
        self._asm = RxAssembler(is_fd)
        self._rxq: queue.Queue = queue.Queue()
        self._fcq: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._send_lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._pump, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _pump(self):
        while not self._stop.is_set():
            frame = self.driver.recv(0.05)
            now = time.time()
            if frame is None:
                if self._asm.expired(now, self.n_cr_s):
                    self.log("ISO-TP 重组超时（N_Cr），丢弃当前报文")
                    self._asm.reset()
                continue
            if self.tap:
                self.tap(frame, True)
            if frame.can_id != self.rx_id or not frame.data:
                continue
            pci = frame.data[0] >> 4
            if pci == FC:
                self._fcq.put(frame.data)
                continue
            if pci == FF:
                self._send_frame(bytes([0x30, self.fc_bs, self.fc_stmin_ms]))
            payload = self._asm.feed(frame.data, now)
            if payload is not None:
                self._rxq.put(payload)

    def _send_frame(self, data: bytes):
        frame = CanFrame(self.tx_id, data, is_fd=self.is_fd, bitrate_switch=self.is_fd)
        with self._send_lock:
            self.driver.send(frame)
        if self.tap:
            self.tap(frame, False)

    def clear_rx(self):
        self._asm.reset()
        self._drain(self._rxq)
        self._drain(self._fcq)

    @staticmethod
    def _drain(q):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def send_payload(self, payload: bytes):
        self.clear_rx()
        frames = encode(payload, self.is_fd)
        self._send_frame(frames[0])
        if len(frames) == 1:
            return
        bs, stmin_s = self._wait_fc(time.time() + self.n_bs_s)
        for cf in frames[1:]:
            if bs is not None and bs <= 0:
                bs, stmin_s = self._wait_fc(time.time() + self.n_bs_s)
            if stmin_s:
                time.sleep(stmin_s)
            self._send_frame(cf)
            if bs is not None:
                bs -= 1

    def _wait_fc(self, deadline: float):
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError("等待流控帧 FC 超时（N_Bs）")
            try:
                data = self._fcq.get(timeout=remain)
            except queue.Empty:
                raise TimeoutError("等待流控帧 FC 超时（N_Bs）")
            if data[0] == 0x30:
                bs = data[1]
                stmin_s = _decode_stmin(data[2] if len(data) > 2 else 0)
                return (None if bs == 0 else bs), stmin_s
            if data[0] == 0x31:  # Wait，继续等
                continue
            raise IsoTpError(f"FC 溢出状态 0x{data[0]:02X}，中止发送")

    def recv_payload(self, timeout_s: float):
        deadline = time.time() + timeout_s
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                return None
            try:
                return self._rxq.get(timeout=remain)
            except queue.Empty:
                return None
