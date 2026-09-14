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
