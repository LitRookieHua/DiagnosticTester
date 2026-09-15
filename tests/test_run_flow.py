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
        req = d[1:1 + d[0]]  # 按SF PCI长度截取（帧补齐到8字节）
        if req == bytes.fromhex("1003"):
            mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
        elif req == bytes.fromhex("22F195"):
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
    assert mock.sent[0].data == bytes([2, 0x10, 0x03]) + bytes(5)  # SF 补齐到 8 字节
    assert mock.sent[1].data == bytes([2, 0x3E, 0x80]) + bytes(5)
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


SEC_CONFIG = """
can: {device: mock, channel: 0, fd: true, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
steps:
  - {name: 解锁, security_access: app}
on_fail: abort
"""

SEED = bytes.fromhex("2CBA4F594105BBF56B52B17757369A46")


def _parse_sf(data):
    # SF 解析：经典 1 字节 PCI（≤7B）与 FD 转义形式（8-62B）
    if data[0] == 0x00:
        return bytes(data[2:2 + data[1]])
    return bytes(data[1:1 + (data[0] & 0x0F)])


def _sf_rx(payload):
    # 构造响应帧 data：≤7B 用 1 字节 PCI 补齐到 8，8-62B 用 FD 转义形式
    if len(payload) <= 7:
        return bytes([len(payload)]) + payload + bytes(7 - len(payload))
    return bytes([0x00, len(payload)]) + payload


class SecEcu:
    """模拟 ECU 0x27：回固定种子，按算法校验密钥。"""

    def __init__(self, mock, seed, accept=True):
        from security import compute_key
        self.mock, self.seed, self.accept = mock, seed, accept
        self._compute = compute_key

    def __call__(self, frame):
        req = _parse_sf(frame.data)
        if req[:2] == bytes.fromhex("2701"):
            self.mock.push_rx(0x78C, _sf_rx(bytes.fromhex("6701") + self.seed))
        elif req[:2] == bytes.fromhex("2702"):
            key = req[2:]
            ok = key == self._compute("app", self.seed, key[:16])
            resp = bytes.fromhex("6702") if (ok and self.accept) else bytes.fromhex("7F2735")
            self.mock.push_rx(0x78C, _sf_rx(resp))


def test_security_step_unlock_pass(tmp_path):
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(SEC_CONFIG, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED)
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 1, "pass": 1, "fail": 0, "timeout": 0}
    # 密钥请求应为 34B payload 的 FD 转义 SF：00 22 27 02 ...
    key_frame = mock.sent[1]
    assert key_frame.data[:3] == bytes([0x00, 34, 0x27])
    csv_text = (tmp_path / "result.csv").read_text(encoding="utf-8")
    assert "PASS" in csv_text and "2701+2702" in csv_text
    log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert SEED.hex().upper() in log_text  # 种子记入日志


def test_security_step_wrong_key_fail(tmp_path):
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(SEC_CONFIG, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED, accept=False)
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 1, "timeout": 0}
    assert "FAIL" in (tmp_path / "result.csv").read_text(encoding="utf-8")


def test_security_step_seed_timeout_fail(tmp_path):
    # 取种子无响应：UnlockError → 判 FAIL（abort 中止流程）
    cfg_path = tmp_path / "flow.yaml"
    cfg_path.write_text(SEC_CONFIG, encoding="utf-8")
    cfg = load_config(cfg_path)
    mock = MockCanDriver()  # 不回帧
    summary = run_flow(cfg, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 1, "timeout": 0}
    assert "FAIL" in (tmp_path / "result.csv").read_text(encoding="utf-8")
