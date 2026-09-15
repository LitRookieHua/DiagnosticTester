import pytest

import diag
from mock_driver import MockCanDriver
from security import compute_key

CONFIG = """
can: {device: mock, channel: 0, fd: true, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
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

    def __call__(self, frame):
        req = _parse_sf(frame.data)
        if req[:2] == bytes.fromhex("2701"):
            self.mock.push_rx(0x78C, _sf_rx(bytes.fromhex("6701") + self.seed))
        elif req[:2] == bytes.fromhex("2702"):
            key = req[2:]
            ok = key == compute_key("app", self.seed, key[:16])
            resp = bytes.fromhex("6702") if (ok and self.accept) else bytes.fromhex("7F2735")
            self.mock.push_rx(0x78C, _sf_rx(resp))


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    """返回 runner(text, ecu)：写配置并执行 main，返回 (exit_code, stdout)。"""
    monkeypatch.setattr(diag, "LOGS_DIR", tmp_path)

    def runner(argv, ecu=None):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(CONFIG, encoding="utf-8")
        if ecu is not None:
            monkeypatch.setattr(diag, "make_driver", lambda cfg: ecu)
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = diag.main([*argv, "--config", str(cfg_path)])
        return code, out.getvalue()

    return runner


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


def test_unlock_bad_level(run_cli):
    # argparse choices 校验非法等级：SystemExit(2)
    with pytest.raises(SystemExit) as e:
        run_cli(["unlock", "ota"])
    assert e.value.code != 0


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
