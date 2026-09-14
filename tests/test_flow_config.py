import pytest

from flow import ConfigError, judge, load_config
from uds import UdsResult

BASE = """
can: {device: USBCANFD-200U, channel: 0, fd: false, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C, functional_id: 0x7DF}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
steps:
  - {name: 进扩展会话, request: "1003", expect: "5003"}
  - {name: 读DID, request: "22F195", expect: "62F195"}
on_fail: abort
"""


def write(tmp_path, text):
    p = tmp_path / "flow.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_ok(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    assert cfg["diagnostic"]["request_id"] == 0x70C
    assert cfg["diagnostic"]["response_id"] == 0x78C
    assert cfg["steps"][0].request == bytes.fromhex("1003")
    assert cfg["steps"][0].expect == bytes.fromhex("5003")
    assert cfg["on_fail"] == "abort"


def test_defaults(tmp_path):
    text = BASE.replace("timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}", "")
    cfg = load_config(write(tmp_path, text))
    assert cfg["timing"] == {"p2_ms": 50, "p2star_ms": 5000, "step_delay_ms": 100}
    assert cfg["on_fail"] == "abort"


def test_missing_step_field(tmp_path):
    text = BASE.replace('  - {name: 读DID, request: "22F195", expect: "62F195"}',
                        '  - {name: 读DID}')
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, text))
    assert "steps[1].request" in str(e.value)


def test_bad_hex(tmp_path):
    text = BASE.replace('"1003"', '"10G3"')
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, text))
    assert "steps[0].request" in str(e.value)


def test_unknown_step_field(tmp_path):
    text = BASE.replace('expect: "5003"}', 'expect: "5003", extra: 1}')
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_bad_addressing(tmp_path):
    text = BASE.replace('request: "1003"', 'request: "1003", addressing: broadcast')
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, text))
    assert "addressing" in str(e.value)


def test_functional_requires_id(tmp_path):
    text = BASE.replace("functional_id: 0x7DF", "")
    text = text.replace('request: "1003"', 'request: "1003", addressing: functional')
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, text))
    assert "functional_id" in str(e.value)


def test_functional_over_sf_rejected(tmp_path):
    text = BASE.replace('request: "1003"', 'request: "10031122334455667788", addressing: functional')
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, text))
    assert "单帧" in str(e.value)


def test_fd_data_baudrate_default(tmp_path):
    text = BASE.replace("fd: false, baudrate: 500000",
                        "fd: true, baudrate: 500000, data_baudrate: 2000000")
    cfg = load_config(write(tmp_path, text))
    assert cfg["can"]["data_baudrate"] == 2000000


class S:
    pass


def test_judge_prefix_match():
    s = S(); s.expect = bytes.fromhex("62F195")
    r = UdsResult(response=bytes.fromhex("62F1950102"))
    assert judge(s, r) == "PASS"
    s2 = S(); s2.expect = bytes.fromhex("62F196")
    assert judge(s2, r) == "FAIL"


def test_judge_intentional_nrc_pass():
    s = S(); s.expect = bytes.fromhex("7F2735")
    r = UdsResult(response=bytes.fromhex("7F2735"), nrc=0x35)
    assert judge(s, r) == "PASS"


def test_judge_timeout_and_no_expect():
    s = S(); s.expect = None
    assert judge(s, UdsResult(timeout=True, elapsed_ms=50)) == "PASS"  # 无期望即记录
    s2 = S(); s2.expect = bytes.fromhex("5003")
    assert judge(s2, UdsResult(timeout=True)) == "TIMEOUT"