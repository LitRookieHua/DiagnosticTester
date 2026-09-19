"""UDS 语义层：请求/响应、NRC 解析、0x78 循环等待、P2/P2* 超时。"""
from __future__ import annotations

import time
from dataclasses import dataclass

# 多帧重组期间每次顺延的等待窗（重组间隙由传输层 N_Cr 保护）
_ASSEMBLY_POLL_S = 0.05


@dataclass
class UdsResult:
    response: bytes | None = None
    nrc: int | None = None
    timeout: bool = False
    elapsed_ms: float = 0.0
    pendings: int = 0

    @property
    def ok(self) -> bool:
        """True 表示收到非空、非 0x7F 打头的响应且未超时。

        0x7F 打头一律不 ok（含缺 NRC 字节的截断负响应如 7F22）；
        空响应与超时也不 ok。
        """
        return bool(self.response) and self.response[0] != 0x7F and not self.timeout


class UdsClient:
    def __init__(self, transport, p2_ms=50, p2star_ms=5000, log=None):
        self.transport = transport
        self.p2_ms = p2_ms
        self.p2star_ms = p2star_ms
        self.log = log or (lambda *a, **k: None)

    def request(self, payload: bytes, timeout_ms: int | None = None) -> UdsResult:
        """发送 UDS 请求并等待响应，期间处理 0x78 pending 循环。

        - timeout_ms 只覆盖 P2（单响应超时），不约束收到首个 0x78 后的
          P2* 等待；未指定时用 self.p2_ms。timeout_ms=0 表示发送后
          立即判定超时。
        - P2 只约束到响应首帧（FF 到达）：多帧响应的 CF 重组由传输层
          N_Cr 保护，重组期间（transport.rx_pending()）自动顺延 deadline，
          不会把传输中途的报文判超时（真机上 228 字节位图需 ~320ms）。
        - P2* 为单个总窗口：从收到首个 0x78 的时刻起算 self.p2star_ms，
          后续 0x78 不重置窗口（设计决策：本工具定位轻量诊断，有界窗口
          防 ECU 无限 pending 导致客户端挂死）。
        - 发送阶段异常（TimeoutError/IsoTpError）直接向上抛出，
          不走 UdsResult.timeout。
        - t0 在发送前采样，多帧发送耗时计入 elapsed_ms。
        - 链路空响应（b""，如全零噪声经 RxAssembler 产生）视为噪声
          丢弃，继续等待至 deadline 兜底。
        """
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
                if (total_deadline is None
                        and self.transport.rx_pending()):
                    # FF 已收、CF 重组中：响应已开始，重组交给 N_Cr
                    deadline = now + _ASSEMBLY_POLL_S
                    continue
                res.timeout = True
                res.elapsed_ms = (now - t0) * 1000.0
                return res
            resp = self.transport.recv_payload(limit - now)
            if resp is None:
                continue  # 回到循环头重查超时
            if not resp:
                continue  # 空响应视为链路噪声，丢弃继续等，deadline 兜底
            if len(resp) >= 3 and resp[0] == 0x7F and resp[2] == 0x78:
                res.pendings += 1
                self.log("NRC 0x78 pending received, keep waiting (P2*)")
                if total_deadline is None:
                    # P2* 锚定在首个 0x78 到达时刻，而非请求发出时刻
                    total_deadline = time.monotonic() + self.p2star_ms / 1000.0
                continue
            res.elapsed_ms = (time.monotonic() - t0) * 1000.0
            res.response = resp
            if len(resp) >= 3 and resp[0] == 0x7F:
                res.nrc = resp[2]
            return res
