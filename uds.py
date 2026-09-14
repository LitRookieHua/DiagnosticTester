"""UDS 语义层：请求/响应、NRC 解析、0x78 循环等待、P2/P2* 超时。"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class UdsResult:
    response: bytes | None = None
    nrc: int | None = None
    timeout: bool = False
    elapsed_ms: float = 0.0
    pendings: int = 0

    @property
    def ok(self) -> bool:
        return self.response is not None and not self.timeout and self.nrc is None


class UdsClient:
    def __init__(self, transport, p2_ms=50, p2star_ms=5000, log=None):
        self.transport = transport
        self.p2_ms = p2_ms
        self.p2star_ms = p2star_ms
        self.log = log or (lambda *a, **k: None)

    def request(self, payload: bytes, timeout_ms: int | None = None) -> UdsResult:
        p2_s = (timeout_ms if timeout_ms is not None else self.p2_ms) / 1000.0
        t0 = time.monotonic()
        self.transport.send_payload(payload)
        res = UdsResult()
        deadline = t0 + p2_s              # P2：单响应超时
        total_deadline = None             # P2*：收到首个 0x78 后的总超时
        while True:
            now = time.monotonic()
            limit = total_deadline if total_deadline is not None else deadline
            if now >= limit:
                res.timeout = True
                res.elapsed_ms = (now - t0) * 1000.0
                return res
            resp = self.transport.recv_payload(limit - now)
            if resp is None:
                continue  # 回到循环头重查超时
            if len(resp) >= 3 and resp[0] == 0x7F and resp[2] == 0x78:
                res.pendings += 1
                self.log("收到 NRC 0x78 pending，继续等待（P2*）")
                if total_deadline is None:
                    total_deadline = t0 + self.p2star_ms / 1000.0
                continue
            res.elapsed_ms = (time.monotonic() - t0) * 1000.0
            res.response = resp
            if len(resp) >= 3 and resp[0] == 0x7F:
                res.nrc = resp[2]
            return res
