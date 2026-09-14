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
    """字节流 → PCI 帧 data 列表（不含 CAN ID）。"""
    if is_fd:
        mtu = 62
        if len(payload) <= mtu:
            return [bytes([0x00, len(payload)]) + payload]
        if len(payload) > 0xFFF:
            raise ValueError("payload 超过 4095 字节未支持")
        head = 0x1000 | len(payload)
        frames = [bytes([(head >> 8) & 0xFF, head & 0xFF]) + payload[:mtu]]
        rest, seq = payload[mtu:], 1
        while rest:
            chunk, rest = rest[:mtu], rest[mtu:]
            frames.append(bytes([0x20 | (seq >> 8 & 0x0F), seq & 0xFF]) + chunk)
            seq = (seq + 1) & 0xFFF
        return frames
    else:
        mtu = 7
        if len(payload) <= mtu:
            return [bytes([len(payload)]) + payload]
        head = 0x1000 | len(payload)
        frames = [bytes([(head >> 8) & 0xFF, head & 0xFF]) + payload[:6]]
        rest, seq = payload[6:], 1
        while rest:
            chunk, rest = rest[:mtu], rest[mtu:]
            frames.append(bytes([0x20 | (seq & 0x0F)]) + chunk)
            seq = (seq + 1) & 0x0F
        return frames
