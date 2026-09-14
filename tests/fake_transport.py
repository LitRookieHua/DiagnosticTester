import time


class FakeTransport:
    """UdsClient 测试替身：send_payload 记录请求，recv_payload 按剧本回放。"""

    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses or [])

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
