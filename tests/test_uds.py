from fake_transport import FakeTransport, DelayedPendingTransport, EndlessPendingTransport
from uds import UdsClient


def test_positive_response():
    ft = FakeTransport([bytes.fromhex("62F1950102")])
    c = UdsClient(ft, p2_ms=200, p2star_ms=5000)
    r = c.request(bytes.fromhex("22F195"))
    assert r.ok
    assert r.response == bytes.fromhex("62F1950102")
    assert r.nrc is None
    assert not r.timeout


def test_step_timeout_override():
    ft = FakeTransport([])
    c = UdsClient(ft, p2_ms=2000, p2star_ms=5000)
    r = c.request(bytes.fromhex("22F195"), timeout_ms=30)
    assert r.timeout
    assert not r.ok
    assert 20 <= r.elapsed_ms < 500


def test_nrc_other_than_78():
    ft = FakeTransport([bytes.fromhex("7F2231")])
    r = UdsClient(ft, p2_ms=200, p2star_ms=5000).request(bytes.fromhex("22F195"))
    assert not r.ok
    assert r.nrc == 0x31


def test_nrc_78_then_positive():
    ft = FakeTransport([bytes.fromhex("7F2278"), bytes.fromhex("62F195AA")])
    r = UdsClient(ft, p2_ms=200, p2star_ms=5000).request(bytes.fromhex("22F195"))
    assert r.ok
    assert r.pendings == 1
    assert r.response == bytes.fromhex("62F195AA")


def test_nrc_78_until_p2star_timeout():
    c = UdsClient(EndlessPendingTransport(), p2_ms=50, p2star_ms=120)
    r = c.request(bytes.fromhex("22F195"))
    assert r.timeout
    assert r.pendings >= 2
    # 钉住 P2*：正确实现下总窗口约 120ms；若 P2* 切换逻辑被删只剩 P2=50ms 会失败
    assert r.elapsed_ms >= 100


def test_empty_response_ignored():
    # 空帧 b""（如全零噪声经 RxAssembler 产生）应被当噪声丢弃，继续等下一帧
    ft = FakeTransport([b"", bytes.fromhex("62F195AA")])
    r = UdsClient(ft, p2_ms=200, p2star_ms=5000).request(bytes.fromhex("22F195"))
    assert r.ok
    assert r.response == bytes.fromhex("62F195AA")


def test_truncated_negative_not_ok():
    # 截断负响应 7F22（缺 NRC 字节）不算成功
    ft = FakeTransport([bytes.fromhex("7F22")])
    r = UdsClient(ft, p2_ms=200, p2star_ms=5000).request(bytes.fromhex("22F195"))
    assert not r.ok
    assert r.response == bytes.fromhex("7F22")


def test_p2star_anchored_at_first_pending():
    # 首个 0x78 在 ~50ms 到达，P2* 总窗口应从该时刻起算（120ms）→ 总时长 ≥170ms 级；
    # 若锚在请求发出时刻 t0，总时长只有 ~120ms，断言失败
    c = UdsClient(DelayedPendingTransport(), p2_ms=1000, p2star_ms=120)
    r = c.request(bytes.fromhex("22F195"))
    assert r.timeout
    assert 150 <= r.elapsed_ms < 2000


def test_request_bytes_passed_through():
    ft = FakeTransport([bytes.fromhex("5003")])
    UdsClient(ft, p2_ms=200, p2star_ms=5000).request(bytes.fromhex("1003"))
    assert ft.requests == [bytes.fromhex("1003")]
