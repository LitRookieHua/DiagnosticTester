import pytest

import DiagnosticTester
from mock_driver import MockCanDriver

CONFIG = """
can: {device: mock, channel: 0, fd: true, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
security:
  app: {seed_sub: 0x01, key_sub: 0x02, algorithm: aes_cmac_df_token,
        params: {key_factor: "5E4741955FEF78D046D754F3CDEF170B"}}
"""

TESTCASE = """
steps:
  - {name: s1, request: "1003", expect: "5003"}
on_fail: abort
"""

SEED = bytes.fromhex("2CBA4F594105BBF56B52B17757369A46")


def _parse_sf(data):
    if data[0] == 0x00:
        return bytes(data[2:2 + data[1]])
    return bytes(data[1:1 + (data[0] & 0x0F)])


def _sf_rx(payload):
    if len(payload) <= 7:
        return bytes([len(payload)]) + payload + bytes(7 - len(payload))
    return bytes([0x00, len(payload)]) + payload


class SecEcu:
    """模拟 ECU 0x27：回固定种子，按算法校验密钥。accept=False 时密钥恒错。"""

    def __init__(self, mock, seed, accept=True):
        self.mock, self.seed, self.accept = mock, seed, accept
        from security import levels_from_config
        self.level = levels_from_config({
            "app": {"seed_sub": 0x01, "key_sub": 0x02,
                    "algorithm": "aes_cmac_df_token",
                    "params": {"key_factor":
                               "5E4741955FEF78D046D754F3CDEF170B"}}})["app"]

    def __call__(self, frame):
        req = _parse_sf(frame.data)
        if req[:2] == bytes([0x27, self.level.seed_sub]):
            self.mock.push_rx(0x78C, _sf_rx(
                bytes([0x67, self.level.seed_sub]) + self.seed))
        elif req[:2] == bytes([0x27, self.level.key_sub]):
            key = req[2:]
            df = key[:16] if len(key) == 32 else None
            ok = key == self.level.compute_key(self.seed, df)
            resp = (bytes([0x67, self.level.key_sub]) if (ok and self.accept)
                    else bytes.fromhex("7F2735"))
            self.mock.push_rx(0x78C, _sf_rx(resp))


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    """返回 runner(argv, ecu=None)：写配置与用例并执行 main，返回 (code, stdout)。
    argv 中的 "tc" 占位符替换为用例文件路径。"""
    monkeypatch.setattr(DiagnosticTester, "LOGS_DIR", tmp_path)

    def runner(argv, ecu=None, cfg_text=CONFIG, tc_text=TESTCASE,
               cfg_name="cfg.yaml", tc_name="tc.yaml"):
        cfg_path = tmp_path / cfg_name
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(cfg_text, encoding="utf-8")
        tc_path = tmp_path / tc_name
        tc_path.parent.mkdir(parents=True, exist_ok=True)
        tc_path.write_text(tc_text, encoding="utf-8")
        if ecu is not None:
            monkeypatch.setattr(DiagnosticTester, "make_driver", lambda cfg: ecu)
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            argv = [str(tc_path) if a == "tc" else a for a in argv]
            code = DiagnosticTester.main([*argv, "--config", str(cfg_path)])
        return code, out.getvalue()

    return runner


def test_run_smoke(run_cli):
    mock = MockCanDriver()

    def on_send(frame):
        d = frame.data
        req = d[1:1 + d[0]]
        if req == bytes.fromhex("1003"):
            mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))

    mock.on_send = on_send
    code, out = run_cli(["run", "tc"], ecu=mock)
    assert code == 0
    assert "PASS 1 / FAIL 0 / TIMEOUT 0 / 共 1/1" in out


def test_run_log_dir_beside_testcase_yaml(run_cli, tmp_path):
    # run 的日志目录 = 用例 yaml 同目录下的时间戳子目录
    mock = MockCanDriver()
    run_cli(["run", "tc"], ecu=mock, cfg_name="n60/n60.yaml",
            tc_name="t1lfl1/dem_read/dem_read.yaml")
    case_dir = tmp_path / "t1lfl1" / "dem_read"
    assert any(p.is_dir() and p != case_dir for p in case_dir.iterdir()), \
        "用例文件夹下未生成时间戳运行目录"


def test_probe_log_dir_uses_project(run_cli, tmp_path):
    # chat/probe/unlock/dem 的日志目录 = 项目名_命令名
    mock = MockCanDriver()  # 无响应超时无妨，目录已建
    run_cli(["probe", "1003"], ecu=mock, cfg_name="rx/rx.yaml")
    assert any(p.is_dir() and p.name.startswith("rx_probe_")
               for p in tmp_path.iterdir())


def test_run_missing_testcase_rejected(run_cli):
    # 缺 testcase 位置参数（fixture 恒附 --config）：argparse 报错 SystemExit(2)
    with pytest.raises(SystemExit) as e:
        run_cli(["run"])
    assert e.value.code != 0


def test_unlock_app_success(run_cli):
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED)
    code, out = run_cli(["unlock", "app"], ecu=mock)
    assert code == 0
    assert SEED.hex().upper() in out
    assert "6702" in out


def test_unlock_wrong_key_nrc(run_cli):
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED, accept=False)
    code, out = run_cli(["unlock", "app"], ecu=mock)
    assert code == 2
    assert "7F2735" in out or "0x35" in out


def test_unlock_seed_timeout(run_cli):
    mock = MockCanDriver()  # 不回帧
    code, _ = run_cli(["unlock", "app"], ecu=mock)
    assert code == 1


def test_unlock_bad_level(run_cli, capsys):
    # 等级来自配置：未知等级报配置错误（退出码 2，提示走 stderr），无需硬件
    code, out = run_cli(["unlock", "ota"])
    assert code == 2
    assert "ota" in capsys.readouterr().err


def test_chat_unlock_command(run_cli, monkeypatch):
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED)
    inputs = iter(["unlock app", ":q"])

    class FakeInput:
        def __init__(self):
            self.prompt = None

        def __call__(self, prompt=""):
            self.prompt = prompt
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError

    import builtins
    monkeypatch.setattr(builtins, "input", FakeInput())
    code, out = run_cli(["chat"], ecu=mock)
    assert code == 0
    assert "6702" in out


def test_chat_unlock_bad_level_continues(run_cli, monkeypatch):
    mock = MockCanDriver()
    inputs = iter(["unlock ota", ":q"])

    class FakeInput:
        def __call__(self, prompt=""):
            try:
                return next(inputs)
            except StopIteration:
                raise EOFError

    import builtins
    monkeypatch.setattr(builtins, "input", FakeInput())
    code, out = run_cli(["chat"], ecu=mock)
    assert code == 0
    assert "ota" in out  # 未知等级应提示而不是崩溃


# ---------- dem ----------

DEM_RESP = {
    "1003": "50030032",
    "19020F": "5902FFEA07882F",
    "222A0D": "622A0D21",
    "222A0E": "622A0E00",
    "222A0F": "622A0F0401",
    "222A1D": "622A1D02",
}


def _dem_ecu(mock):
    """按 DEM_RESP 脚本回帧的假 ECU。"""
    def on_send(frame):
        req = _parse_sf(frame.data)
        resp = DEM_RESP.get(req.hex().upper())
        if resp is not None:
            mock.push_rx(0x78C, _sf_rx(bytes.fromhex(resp)))
    return on_send


def test_dem_report(run_cli, tmp_path):
    (tmp_path / "dtc_map.yaml").write_text(
        "0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff\n", encoding="utf-8")
    (tmp_path / "event_map.yaml").write_text(
        "1: DemEvent_HCTDTC_1\n", encoding="utf-8")
    (tmp_path / "fimfid_map.yaml").write_text(
        "2: FiM_Function_Xxx\n", encoding="utf-8")
    mock = MockCanDriver()
    mock.on_send = _dem_ecu(mock)
    code, out = run_cli(["dem"], ecu=mock)
    assert code == 0
    assert "DemDTC_U2A0788_CAN_ACAN_Busoff" in out
    assert "1: DemEvent_HCTDTC_1" in out
    assert "6:（未在 event_map 中）" in out
    assert "（无置位 event）" in out
    assert "2: FiM_Function_Xxx" in out


def test_dem_all_timeout_exit_1(run_cli):
    mock = MockCanDriver()  # 不回帧（含 1003），会话 + 五项全超时
    code, out = run_cli(["dem"], ecu=mock)
    assert code == 1
    assert out.count("UDS RX: (timeout)") == 6
    assert "进扩展会话失败" in out

def test_dem_enters_extended_session_first(run_cli, tmp_path):
    # dem 先发 10 03 进扩展会话，且会话失败不阻断后续读取
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "dtc_map.yaml").write_text(
        "0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff\n", encoding="utf-8")
    mock = MockCanDriver()
    mock.on_send = _dem_ecu(mock)
    code, out = run_cli(["dem"], ecu=mock, cfg_name="configs/cfg.yaml")
    assert code == 0
    assert "[DTC 故障]" in out  # 1003 成功后照常五项
    log_dirs = [p for p in tmp_path.iterdir()
                if p.is_dir() and p.name.startswith("cfg_dem_")]
    log_text = (log_dirs[0] / "run.log").read_text(encoding="utf-8")
    assert "UDS TX: 10 03" in log_text


def test_dem_bad_map_exit_2(run_cli, tmp_path, capsys):
    # map 文件校验失败 → main 的 ConfigError 处理 → 退出码 2，不触硬件
    (tmp_path / "dtc_map.yaml").write_text("0xEA0788: 123\n", encoding="utf-8")
    code, _ = run_cli(["dem"])
    assert code == 2
    assert "dtc_map" in capsys.readouterr().err


# ---------- dem-map（手动粘贴离线解析，不触硬件） ----------

def _feed_inputs(monkeypatch, lines):
    inputs = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    import builtins
    monkeypatch.setattr(builtins, "input", fake_input)


def _write_maps(tmp_path):
    (tmp_path / "configs").mkdir(exist_ok=True)
    (tmp_path / "configs" / "dtc_map.yaml").write_text(
        "0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff\n", encoding="utf-8")
    (tmp_path / "configs" / "event_map.yaml").write_text(
        "1: DemEvent_HCTDTC_1\n", encoding="utf-8")
    (tmp_path / "configs" / "fimfid_map.yaml").write_text(
        "2: FiM_Function_Xxx\n", encoding="utf-8")


def test_dem_map_decodes_all_items(run_cli, tmp_path, monkeypatch):
    _write_maps(tmp_path)
    # 五项依次：DTC 整段响应 / 当前 event 整段响应 / 本周期裸 payload /
    # 历史 event 回车跳过 / fimfid 裸 payload
    _feed_inputs(monkeypatch, [
        "5902FFEA07882F", "622A0D21", "21", "", "02"])
    code, out = run_cli(["dem-map"], cfg_name="configs/cfg.yaml")
    assert code == 0
    assert "[DTCStatus(190209)]" in out
    assert "EA0788  DemDTC_U2A0788_CAN_ACAN_Busoff  0x2F" in out
    assert "[CurrentEventStatus(222A0D)]" in out
    assert "1: DemEvent_HCTDTC_1" in out
    assert "6:（未在 event_map 中）" in out
    assert "[HistoryEventStatus(222A0F)]" in out
    assert "（跳过）" in out
    assert "[FiMFIDStatus(222A1D)]" in out
    assert "2: FiM_Function_Xxx" in out


def test_dem_map_bad_hex_exit_1(run_cli, tmp_path, monkeypatch):
    _write_maps(tmp_path)
    _feed_inputs(monkeypatch, ["ZZ", "", "", "", ""])
    code, out = run_cli(["dem-map"], cfg_name="configs/cfg.yaml")
    assert code == 1
    assert "输入错误" in out


def test_dem_map_dtc_parse_failure_exit_1(run_cli, tmp_path, monkeypatch):
    _write_maps(tmp_path)
    # 不是 59 02 正响应 → 解析失败，退出码 1
    _feed_inputs(monkeypatch, ["1234", "", "", "", ""])
    code, out = run_cli(["dem-map"], cfg_name="configs/cfg.yaml")
    assert code == 1
    assert "解析失败" in out


def test_dem_report_written_to_run_log(run_cli, tmp_path):
    # 配置放 configs/ 根 → 项目名取文件名 "cfg"，日志前缀 cfg_dem_
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "dtc_map.yaml").write_text(
        "0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff\n", encoding="utf-8")
    mock = MockCanDriver()
    mock.on_send = _dem_ecu(mock)
    code, _ = run_cli(["dem"], ecu=mock, cfg_name="configs/cfg.yaml")
    assert code == 0
    log_dirs = [p for p in tmp_path.iterdir()
                if p.is_dir() and p.name.startswith("cfg_dem_")]
    assert log_dirs, "dem 日志目录未创建"
    log_text = (log_dirs[0] / "run.log").read_text(encoding="utf-8")
    assert "===== DEM 故障报告 =====" in log_text
    assert "DemDTC_U2A0788_CAN_ACAN_Busoff" in log_text


def test_dem_log_dir_uses_project(run_cli, tmp_path):
    mock = MockCanDriver()  # 无响应无妨，目录已建
    run_cli(["dem"], ecu=mock, cfg_name="rx/rx.yaml")
    assert any(p.is_dir() and p.name.startswith("rx_dem_")
               for p in tmp_path.iterdir())
