from fake_transport import FakeTransport, EndlessPendingTransport
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


def test_request_bytes_passed_through():
    ft = FakeTransport([bytes.fromhex("5003")])
    UdsClient(ft, p2_ms=200, p2star_ms=5000).request(bytes.fromhex("1003"))
    assert ft.requests == [bytes.fromhex("1003")]
