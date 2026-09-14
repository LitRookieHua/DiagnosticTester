import pytest

from flow import load_config, run_flow
from mock_driver import MockCanDriver

CONFIG = """
can: {device: mock, channel: 0, fd: false, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C, functional_id: 0x7DF}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
steps:
  - {name: s1, request: "1003", expect: "5003"}
  - {name: s2, request: "22F195", expect: "62F195"}
on_fail: abort
"""


def test_run_flow_pass(tmp_path):
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(CONFIG, encoding="utf-8")
    cfg = load_config(cfg_path)

    mock = MockCanDriver()

    def on_send(frame):
        d = frame.data
        if d[1:] == bytes.fromhex("1003"):
            mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
        elif d[1:] == bytes.fromhex("22F195"):
            mock.push_rx(0x78C, bytes([5]) + bytes.fromhex("62F1950102"))

    mock.on_send = on_send
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 2, "pass": 2, "fail": 0, "timeout": 0}
    assert (tmp_path / "run.log").exists()
    assert (tmp_path / "result.csv").exists()
    assert (tmp_path / "trace.blf").exists()


def test_on_fail_abort_stops(tmp_path):
    # s1 期望改成不匹配的 62xx → FAIL → abort，s2 不再执行
    text = CONFIG.replace('request: "1003", expect: "5003"',
                          'request: "1003", expect: "62"')
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()
    mock.on_send = lambda f: mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
    summary = run_flow(cfg, mock, tmp_path)
    assert summary["total"] == 1
    assert summary["fail"] == 1
    assert len(mock.sent) == 1


def test_on_fail_continue_runs_all(tmp_path):
    # 与 abort 测试同样把 s1 期望改成不匹配的 62xx，两步都 FAIL；
    # continue 模式下仍应全部执行（total=2, fail=2）
    text = CONFIG.replace('request: "1003", expect: "5003"',
                          'request: "1003", expect: "62"')
    text = text.replace("on_fail: abort", "on_fail: continue")
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()
    mock.on_send = lambda f: mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
    summary = run_flow(cfg, mock, tmp_path)
    assert summary["total"] == 2
    assert summary["fail"] == 2


def test_functional_step_no_wait(tmp_path):
    # 两步都是功能寻址：直接发送、不等待响应，应全 PASS
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(CONFIG.replace(
        '  - {name: s1, request: "1003", expect: "5003"}\n'
        '  - {name: s2, request: "22F195", expect: "62F195"}',
        '  - {name: s1, request: "1003", addressing: functional}\n'
        '  - {name: s2, request: "3E80", addressing: functional}'), encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 2, "pass": 2, "fail": 0, "timeout": 0}
    assert mock.sent[0].can_id == 0x7DF
    assert mock.sent[0].data == bytes([2, 0x10, 0x03])
    assert mock.sent[1].data == bytes([2, 0x3E, 0x80])
    # 功能寻址步骤也须记入 CSV（verdict=SENT），行数与步骤对齐
    csv_text = (tmp_path / "result.csv").read_text(encoding="utf-8")
    rows = [line for line in csv_text.splitlines() if line.strip()]
    assert len(rows) == 3  # 表头 + 2 步
    assert "s1" in csv_text and "s2" in csv_text
    assert csv_text.count("SENT") == 2


def test_run_flow_exception_flushes_blf(tmp_path):
    # 发送阶段驱动抛异常：异常向上抛，但 BLF/run.log 必须已落盘（不因泄漏而丢失）
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(CONFIG, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()

    def on_send(frame):
        raise RuntimeError("boom")

    mock.on_send = on_send
    with pytest.raises(RuntimeError):
        run_flow(cfg, mock, tmp_path)
    blf = tmp_path / "trace.blf"
    assert blf.exists() and blf.stat().st_size > 0
    assert (tmp_path / "run.log").exists()


def test_timeout_triggers_abort(tmp_path):
    # 无响应且带 expect：TIMEOUT 也应中止后续步骤（on_fail=abort）并记入 CSV
    text = CONFIG.replace(
        '  - {name: s1, request: "1003", expect: "5003"}\n'
        '  - {name: s2, request: "22F195", expect: "62F195"}',
        '  - {name: s1, request: "1003", expect: "5003"}')
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()  # 不回帧
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 0, "timeout": 1}
    assert "TIMEOUT" in (tmp_path / "result.csv").read_text(encoding="utf-8")


def test_nrc_recorded_in_csv(tmp_path):
    # 负响应的 NRC 应记入 CSV 的 nrc 列，且判 FAIL
    text = CONFIG.replace(
        '  - {name: s1, request: "1003", expect: "5003"}\n'
        '  - {name: s2, request: "22F195", expect: "62F195"}',
        '  - {name: s1, request: "22F195", expect: "62F195"}')
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()
    mock.on_send = lambda f: mock.push_rx(0x78C, bytes([3]) + bytes.fromhex("7F2231"))
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 1, "timeout": 0}
    csv_text = (tmp_path / "result.csv").read_text(encoding="utf-8")
    assert "0x31" in csv_text
    assert "FAIL" in csv_text
