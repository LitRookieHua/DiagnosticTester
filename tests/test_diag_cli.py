import pytest

import diag
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
    monkeypatch.setattr(diag, "LOGS_DIR", tmp_path)

    def runner(argv, ecu=None, cfg_text=CONFIG, tc_text=TESTCASE,
               cfg_name="cfg.yaml"):
        cfg_path = tmp_path / cfg_name
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(cfg_text, encoding="utf-8")
        tc_path = tmp_path / "tc.yaml"
        tc_path.write_text(tc_text, encoding="utf-8")
        if ecu is not None:
            monkeypatch.setattr(diag, "make_driver", lambda cfg: ecu)
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            argv = [str(tc_path) if a == "tc" else a for a in argv]
            code = diag.main([*argv, "--config", str(cfg_path)])
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


def test_run_log_dir_uses_project_and_testcase_stem(run_cli, tmp_path):
    # 日志目录前缀 = 项目名（config 子目录）_ testcase 文件名
    mock = MockCanDriver()
    run_cli(["run", "tc"], ecu=mock, cfg_name="BAIC_N60/n60.yaml")
    assert any(p.is_dir() and p.name.startswith("BAIC_N60_tc_")
               for p in tmp_path.iterdir())


def test_run_log_dir_configs_root_uses_config_stem(run_cli, tmp_path):
    # 配置直接放在 configs/ 根下时项目名取配置文件名
    mock = MockCanDriver()
    run_cli(["run", "tc"], ecu=mock, cfg_name="configs/cfg.yaml")
    assert any(p.is_dir() and p.name.startswith("cfg_tc_")
               for p in tmp_path.iterdir())


def test_probe_log_dir_uses_project(run_cli, tmp_path):
    # chat/probe/unlock 的日志目录 = 项目名_命令名
    mock = MockCanDriver()  # 无响应超时无妨，目录已建
    run_cli(["probe", "1003"], ecu=mock, cfg_name="CMC_RX/rx.yaml")
    assert any(p.is_dir() and p.name.startswith("CMC_RX_probe_")
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
    "19020F": "5902FFEA07882F",
    "222A0D": "622A0D21",
    "222A0E": "622A0E00",
    "222A0F": "622A0F0401",
    "222A1E": "622A1E02",
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
    mock = MockCanDriver()  # 不回帧
    code, out = run_cli(["dem"], ecu=mock)
    assert code == 1
    assert out.count("(超时无响应)") == 5


def test_dem_log_dir_uses_project(run_cli, tmp_path):
    mock = MockCanDriver()  # 无响应无妨，目录已建
    run_cli(["dem"], ecu=mock, cfg_name="CMC_RX/rx.yaml")
    assert any(p.is_dir() and p.name.startswith("CMC_RX_dem_")
               for p in tmp_path.iterdir())
