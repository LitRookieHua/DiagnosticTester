import time


class FakeTransport:
    """UdsClient 测试替身：send_payload 记录请求，recv_payload 按剧本回放。"""

    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses or [])

    def rx_pending(self):
        return False

    def send_payload(self, payload):
        self.requests.append(payload)

    def recv_payload(self, timeout_s):
        if self.responses:
            return self.responses.pop(0)
        time.sleep(timeout_s)
        return None

class EndlessPendingTransport(FakeTransport):
    """recv 永远立即回 0x78 pending。"""

    def recv_payload(self, timeout_s):
        return bytes.fromhex("7F2278")


class DelayedPendingTransport(FakeTransport):
    """首个 recv 延迟 50ms 后回 0x78 pending，之后按空剧本 sleep 返回 None。

    用于验证 P2* 总窗口锚定在首个 0x78 到达时刻而非请求发出时刻：
    延迟只加在首个 recv 上，避免后续 recv 的额外延迟掩盖锚点差异。
    """

    def __init__(self):
        super().__init__([bytes.fromhex("7F2278")])

    def recv_payload(self, timeout_s):
        if self.responses:
            time.sleep(0.05)
        return super().recv_payload(timeout_s)


class SlowAssemblyTransport(FakeTransport):
    """多帧响应重组耗时超过 P2：rx_pending 到期前为 True，之后回完整响应。

    模拟真机上 22 2A0D 的位图响应：FF 立即到达，但 32 个 CF 需 ~320ms
    重组完成；P2 只应约束到响应首帧（FF），不应把重组中途的报文判超时。
    """

    def __init__(self, response, assemble_s):
        super().__init__([response])
        self._deadline = time.monotonic() + assemble_s

    def rx_pending(self):
        return time.monotonic() < self._deadline

    def recv_payload(self, timeout_s):
        if time.monotonic() < self._deadline:
            time.sleep(min(timeout_s, 0.005))
            return None
        return super().recv_payload(timeout_s)
