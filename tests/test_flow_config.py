import pytest

from flow import ConfigError, judge, load_config, load_testcase
from uds import UdsResult

CFG = """
can: {device: USBCANFD-200U, channel: 0, fd: false, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C, functional_id: 0x7DF}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
"""

STEPS = """
steps:
  - {name: 进扩展会话, request: "1003", expect: "5003"}
  - {name: 读DID, request: "22F195", expect: "62F195"}
on_fail: abort
"""


def load(tmp_path, cfg_text=CFG, tc_text=STEPS):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    tc_path = tmp_path / "tc.yaml"
    tc_path.write_text(tc_text, encoding="utf-8")
    cfg = load_config(cfg_path)
    return cfg, load_testcase(tc_path, cfg)


# ===== 加载与拆分 =====


def test_load_ok(tmp_path):
    cfg, tc = load(tmp_path)
    assert cfg["diagnostic"]["request_id"] == 0x70C
    assert cfg["diagnostic"]["response_id"] == 0x78C
    assert tc["steps"][0].request == bytes.fromhex("1003")
    assert tc["steps"][0].expect == bytes.fromhex("5003")
    assert tc["on_fail"] == "abort"


def test_config_rejects_steps(tmp_path):
    # steps 属于 testcase，出现在配置里必须报未知顶层字段
    p = tmp_path / "cfg.yaml"
    p.write_text(CFG + STEPS, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "steps" in str(e.value)


def test_testcase_rejects_can(tmp_path):
    (tmp_path / "cfg.yaml").write_text(CFG, encoding="utf-8")
    p = tmp_path / "tc.yaml"
    p.write_text(CFG + STEPS, encoding="utf-8")
    cfg = load_config(tmp_path / "cfg.yaml")
    with pytest.raises(ConfigError) as e:
        load_testcase(p, cfg)
    assert "can" in str(e.value)


def test_timing_defaults(tmp_path):
    text = CFG.replace(
        "timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}", "")
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    cfg = load_config(p)
    assert cfg["timing"] == {"p2_ms": 50, "p2star_ms": 5000, "step_delay_ms": 100}


def test_testcase_timing_override_merges(tmp_path):
    # 覆盖 p2_ms，其余键继承配置
    tc_text = "timing: {p2_ms: 200}\n" + STEPS
    cfg, tc = load(tmp_path, tc_text=tc_text)
    assert tc["timing"] == {"p2_ms": 200, "p2star_ms": 1000,
                            "step_delay_ms": 0}


def test_timing_not_mapping(tmp_path):
    text = CFG.replace("timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}",
                       "timing: [1, 2]")
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "timing" in str(e.value)


def test_timing_p2_negative(tmp_path):
    text = CFG.replace("p2_ms: 100", "p2_ms: -1")
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "p2_ms" in str(e.value)


# ===== security 节 =====

SEC_CFG = CFG + """
security:
  app: {seed_sub: 0x01, key_sub: 0x02, algorithm: aes_cmac_df_token,
        params: {key_factor: "5E4741955FEF78D046D754F3CDEF170B"}}
"""


def test_security_section_loads(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(SEC_CFG, encoding="utf-8")
    cfg = load_config(p)
    assert set(cfg["security_levels"]) == {"app", "develop"}  # develop 内置
    app = cfg["security_levels"]["app"]
    assert app.seed_sub == 0x01 and app.key_sub == 0x02
    assert app.algorithm == "aes_cmac_df_token"


def test_security_bad_level_rejected_as_config_error(tmp_path):
    text = SEC_CFG.replace("seed_sub: 0x01, key_sub: 0x02",
                           "seed_sub: 0x01, key_sub: 0x05")
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "key_sub" in str(e.value)


# ===== steps 校验（testcase 侧） =====


def test_missing_step_field(tmp_path):
    text = STEPS.replace('  - {name: 读DID, request: "22F195", expect: "62F195"}',
                         '  - {name: 读DID}')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "steps[1].request" in str(e.value)


def test_request_unquoted_hex(tmp_path):
    # YAML 里 0x1003 不加引号解析成 int 4099，必须报错而非静默 str() 转成错误字节
    text = STEPS.replace('request: "1003"', "request: 0x1003")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "steps[0].request" in str(e.value)


def test_bad_hex(tmp_path):
    text = STEPS.replace('"1003"', '"10G3"')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "steps[0].request" in str(e.value)


def test_step_timeout_not_number(tmp_path):
    text = STEPS.replace('expect: "5003"}', 'expect: "5003", timeout_ms: "abc"}')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "steps[0].timeout_ms" in str(e.value)


def test_unknown_step_field(tmp_path):
    text = STEPS.replace('expect: "5003"}', 'expect: "5003", extra: 1}')
    with pytest.raises(ConfigError):
        load(tmp_path, tc_text=text)


def test_unknown_top_level_key(tmp_path):
    text = STEPS.replace("on_fail: abort", "on_fial: abort")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "on_fial" in str(e.value)


def test_unknown_can_key(tmp_path):
    # 拼错 baudrate → baudratee：必须报未知字段，不能静默忽略后再报缺失
    text = CFG.replace("baudrate: 500000", "baudratee: 500000")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=text)
    assert "can.baudratee" in str(e.value)
    assert "未知" in str(e.value)


def test_unknown_diagnostic_key(tmp_path):
    text = CFG.replace("functional_id: 0x7DF", "functionl_id: 0x7DF")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=text)
    assert "diagnostic.functionl_id" in str(e.value)


def test_unknown_timing_key(tmp_path):
    text = CFG.replace("p2star_ms: 1000", "p2s_ms: 100")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=text)
    assert "timing.p2s_ms" in str(e.value)


def test_id_out_of_11bit_range(tmp_path):
    text = CFG.replace("request_id: 0x70C", "request_id: 0x800")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=text)
    assert "11 位" in str(e.value)


def test_bad_addressing(tmp_path):
    text = STEPS.replace('request: "1003"',
                        'request: "1003", addressing: broadcast')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "addressing" in str(e.value)


def test_functional_requires_id(tmp_path):
    cfg_text = CFG.replace("functional_id: 0x7DF", "")
    tc_text = STEPS.replace('request: "1003", expect: "5003"',
                            'request: "1003", addressing: functional')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=cfg_text, tc_text=tc_text)
    assert "functional_id" in str(e.value)


def test_functional_with_expect_rejected(tmp_path):
    text = STEPS.replace('request: "1003"', 'request: "1003", addressing: functional')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "expect" in str(e.value)


def test_fd_not_bool(tmp_path):
    text = CFG.replace("fd: false", 'fd: "false"')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=text)
    assert "fd" in str(e.value)


def test_request_id_bool(tmp_path):
    # YAML 的 true 是 bool，是 int 子类，不能被当成 CAN ID 1
    text = CFG.replace("request_id: 0x70C", "request_id: true")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=text)
    assert "request_id" in str(e.value)


def test_functional_over_sf_rejected(tmp_path):
    text = STEPS.replace('request: "1003", expect: "5003"',
                        'request: "10031122334455667788", addressing: functional')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "单帧" in str(e.value)


def test_functional_fd_62_bytes_ok(tmp_path):
    req = "10" + "11" * 61  # 62 字节 = FD 功能寻址单帧上限
    cfg_text = CFG.replace("fd: false", "fd: true")
    tc_text = STEPS.replace('request: "1003", expect: "5003"',
                            f'request: "{req}", addressing: functional')
    _cfg, tc = load(tmp_path, cfg_text=cfg_text, tc_text=tc_text)
    assert len(tc["steps"][0].request) == 62


def test_functional_fd_63_bytes_rejected(tmp_path):
    req = "10" + "11" * 62  # 63 字节，超过 FD 单帧上限
    cfg_text = CFG.replace("fd: false", "fd: true")
    tc_text = STEPS.replace('request: "1003", expect: "5003"',
                            f'request: "{req}", addressing: functional')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=cfg_text, tc_text=tc_text)
    assert "单帧" in str(e.value)


def test_fd_data_baudrate_default(tmp_path):
    text = CFG.replace("fd: false, baudrate: 500000", "fd: true, baudrate: 500000")
    cfg, _tc = load(tmp_path, cfg_text=text)
    assert cfg["can"]["data_baudrate"] == 2000000


def test_steps_empty(tmp_path):
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text="steps: []\non_fail: abort\n")
    assert "steps" in str(e.value)


def test_on_fail_invalid(tmp_path):
    text = STEPS.replace("on_fail: abort", "on_fail: bad")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "on_fail" in str(e.value)


# ===== judge（不变） =====


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
    assert judge(s, UdsResult(timeout=True, elapsed_ms=50)) == "PASS"
    s2 = S(); s2.expect = bytes.fromhex("5003")
    assert judge(s2, UdsResult(timeout=True)) == "TIMEOUT"


# ===== 安全解锁步骤（testcase 侧，等级查配置） =====

SEC_STEP = """
steps:
  - {name: 解锁app, security_access: app}
on_fail: abort
"""


def test_security_step_loads(tmp_path):
    _cfg, tc = load(tmp_path, cfg_text=SEC_CFG, tc_text=SEC_STEP)
    s = tc["steps"][0]
    assert s.security_access == "app"
    assert s.request is None
    assert s.expect is None


def test_security_step_with_request_rejected(tmp_path):
    text = SEC_STEP.replace(
        "- {name: 解锁app, security_access: app}",
        '- {name: 解锁app, security_access: app, request: "2701"}')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=SEC_CFG, tc_text=text)
    assert "security_access" in str(e.value)


def test_security_step_with_expect_rejected(tmp_path):
    text = SEC_STEP.replace(
        "- {name: 解锁app, security_access: app}",
        '- {name: 解锁app, security_access: app, expect: "6702"}')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=SEC_CFG, tc_text=text)
    assert "security_access" in str(e.value)


def test_security_step_bad_level_rejected(tmp_path):
    # 等级必须在配置（+内置 develop）里
    text = SEC_STEP.replace("security_access: app", "security_access: ota")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=SEC_CFG, tc_text=text)
    assert "security_access" in str(e.value)


def test_security_step_develop_builtin(tmp_path):
    # develop 内置：配置无 security 节也能用
    text = SEC_STEP.replace("security_access: app", "security_access: develop")
    _cfg, tc = load(tmp_path, tc_text=text)
    assert tc["steps"][0].security_access == "develop"


def test_security_step_functional_rejected(tmp_path):
    text = SEC_STEP.replace(
        "- {name: 解锁app, security_access: app}",
        "- {name: 解锁app, security_access: app, addressing: functional}")
    with pytest.raises(ConfigError) as e:
        load(tmp_path, cfg_text=SEC_CFG, tc_text=text)
    assert "physical" in str(e.value)
