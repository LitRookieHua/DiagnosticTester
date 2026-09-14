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
