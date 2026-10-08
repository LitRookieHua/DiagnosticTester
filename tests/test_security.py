import pytest

from fake_transport import FakeTransport
from security import (DEV_MAGIC, levels_from_config, perform_unlock,
                      seed_to_key_app, seed_to_key_boot)
from uds import UdsClient

# 金标准向量：N60 真机采集（b81vs 与 n60 的 0x27 实现完全一致，key factor 通用）
V_APP = dict(seed="2CBA4F594105BBF56B52B17757369A46",
             df="695577F95AC21D9F553049E15A455D79",
             key="695577F95AC21D9F553049E15A455D79"
                 "80DADB9465B1403A78140BDDE4924D4D")
V_BOOT = dict(seed="0017E474DD2D1474CCF3F372F193AF51",
              df="5ADE049941330BDED801D69C7619DFBB",
              key="5ADE049941330BDED801D69C7619DFBB"
                  "90E893369DCF99C47C27A38D1D627EF5")
V_DEV = dict(seed="833D96C2A22693B43D75C94B92CB0F4F",
             key="2BF8DE6BF27911AE6346F94FB37500F7")

# N60 项目 security 节（与 configs/n60/n60_zlg.yaml 一致）
N60_SECURITY = {
    "app": {"seed_sub": 0x01, "key_sub": 0x02,
            "algorithm": "aes_cmac_df_token",
            "params": {"key_factor": "5E4741955FEF78D046D754F3CDEF170B"}},
    "boot": {"seed_sub": 0x11, "key_sub": 0x12,
             "algorithm": "aes_cmac_df_token",
             "params": {"key_factor": "A028E273415E3050B8DEF560364A03BD"}},
}

# RX mask_table 算法向量：按 cpj_cmc C 源码逐行转写计算
MASK_TABLE = [0xf7cbc641, 0xc81dc123, 0xd88eb629, 0x942e16ad,
              0x616277ff, 0x8cd16b85, 0x79b09727, 0x7198b307,
              0x7dd79183, 0x2f7f1b17, 0x0eb2ff43, 0x6c6b262d,
              0x4efe3885, 0xd028eabd, 0x0ae30f11, 0x3eea5c0d]
V_MASK_APP = [(0x0000, 0xF7CBC641), (0x0001, 0xA2B9E303),
              (0x1234, 0x54295DB3), (0xABCD, 0xEE33C200),
              (0xFFFF, 0x792E6737)]
V_MASK_BOOT = [(0x0000, 0xF7CBC641), (0x0001, 0xA93C70A5),
               (0x1234, 0x8D5BBB39), (0xABCD, 0x7DE37C8A),
               (0xFFFF, 0x2B985440)]

RX_SECURITY = {
    "app": {"seed_sub": 0x01, "key_sub": 0x02,
            "algorithm": "cmc_mask_app",
            "params": {"mask_table": [f"{v:08x}" for v in MASK_TABLE]}},
}

# T1LFL1 CRC 式位运算向量：按 veh_t1lfl1 ov_dia_0x27_security_access.cpp
# 逐行转写计算（seed 0000 已按 C 语义手工逐步核对）
V_T1LFL1 = [("0000", "9AB1"), ("0001", "1A94"), ("1234", "017A"),
            ("ABCD", "FAA5"), ("FFFF", "8025")]

T1LFL1_SECURITY = {
    "app": {"seed_sub": 0x03, "key_sub": 0x04, "algorithm": "t1lfl1_crc16"},
}

# V2XEU app：AES-CMAC 直算、种子请求不带 magic（与 veh_v2xeu 源码一致）
V2XEU_SECURITY = {
    "app": {"seed_sub": 0x01, "key_sub": 0x02, "algorithm": "aes_cmac_seed",
            "params": {"key": "B6042A5FFBFAED83539F4C42D44CFFDF"}},
}

# GAC T75 app/boot 向量：按 cpj_gac ov_dia_0x27_security_access.cpp
# 的 computeKey 逐行转写计算（常量 app=0x7AFC5EC0 boot=0x3ADC6B07）
V_GAC_APP = [(0x00000000, 0x17B03D7E), (0x00000001, 0x17B03DBE),
             (0x12345678, 0xB0F0E765), (0xAABBCCDD, 0x1B691B7E),
             (0xFFFFFFFF, 0xE84EC281), (0x89ABCDEF, 0x57694E30)]
V_GAC_BOOT = [(0x00000000, 0x1AC1DD6E), (0x00000001, 0x1AC1DDAE),
              (0x12345678, 0xBD814775), (0xAABBCCDD, 0x1618FB6E),
              (0xFFFFFFFF, 0xE53F6291), (0x89ABCDEF, 0x5A18EE20)]

# T75 项目 security 节（与 configs/t75/t75_zlg.yaml 一致；
# develop 为内置 61/62，Security Level 0x31 只是等级标识）
T75_SECURITY = {
    "app": {"seed_sub": 0x01, "key_sub": 0x02, "algorithm": "gac_shift_xor",
            "params": {"constant": "7AFC5EC0"}},
    "boot": {"seed_sub": 0x11, "key_sub": 0x12, "algorithm": "gac_shift_xor",
             "params": {"constant": "3ADC6B07"}},
}


@pytest.fixture
def n60():
    return levels_from_config(N60_SECURITY)


# ===== 算法纯函数向量 =====


def test_seed_to_key_app_vectors():
    for seed, key in V_MASK_APP:
        assert seed_to_key_app(seed, MASK_TABLE) == key, f"seed={seed:04X}"


def test_seed_to_key_boot_vectors():
    for seed, key in V_MASK_BOOT:
        assert seed_to_key_boot(seed, MASK_TABLE) == key, f"seed={seed:04X}"


# ===== 等级构建 =====


def test_levels_develop_builtin(n60):
    # 无 security 节 / 空 dict 时 develop 仍内置可用
    assert "develop" in n60
    assert "develop" in levels_from_config(None)
    assert "develop" in levels_from_config({})
    dev = levels_from_config(None)["develop"]
    assert dev.seed_sub == 0x61 and dev.key_sub == 0x62
    assert dev.seed_len == 16 and dev.key_len == 16
    assert dev.seed_request() == bytes([0x27, 0x61]) + DEV_MAGIC


def test_levels_develop_override():
    levels = levels_from_config({"develop": {
        "seed_sub": 0x61, "key_sub": 0x62, "algorithm": "aes_cmac_seed",
        "params": {"key": "00" * 16, "magic": "11" * 16}}})
    dev = levels["develop"]
    assert dev.seed_request().endswith(bytes.fromhex("11" * 16))
    assert len(dev.compute_key(bytes(16))) == 16


def test_key_sub_must_be_seed_sub_plus_one():
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x01, "key_sub": 0x03,
                                   "algorithm": "cmc_mask_app",
                                   "params": {"mask_table": MASK_TABLE}}})


def test_unknown_algorithm_rejected():
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x01, "key_sub": 0x02,
                                   "algorithm": "xor",
                                   "params": {}}})


def test_missing_params_rejected():
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x01, "key_sub": 0x02,
                                   "algorithm": "aes_cmac_df_token",
                                   "params": {}}})


def test_bad_mask_table_length_rejected():
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x01, "key_sub": 0x02,
                                   "algorithm": "cmc_mask_app",
                                   "params": {"mask_table": MASK_TABLE[:15]}}})


def test_unknown_level_field_rejected():
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x01, "key_sub": 0x02,
                                   "algorithm": "cmc_mask_app",
                                   "params": {"mask_table": MASK_TABLE},
                                   "key_length": 4}})


def test_sub_out_of_range_rejected():
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x00, "key_sub": 0x01,
                                   "algorithm": "cmc_mask_app",
                                   "params": {"mask_table": MASK_TABLE}}})


# ===== 密钥计算 =====


def test_app_key_golden(n60):
    assert n60["app"].compute_key(bytes.fromhex(V_APP["seed"]),
                                  bytes.fromhex(V_APP["df"])) \
        == bytes.fromhex(V_APP["key"])


def test_boot_key_golden(n60):
    assert n60["boot"].compute_key(bytes.fromhex(V_BOOT["seed"]),
                                   bytes.fromhex(V_BOOT["df"])) \
        == bytes.fromhex(V_BOOT["key"])


def test_develop_key_golden():
    dev = levels_from_config(None)["develop"]
    assert dev.compute_key(bytes.fromhex(V_DEV["seed"])) \
        == bytes.fromhex(V_DEV["key"])


def test_cmc_mask_key_bytes(n60):
    # RX app：2 字节种子 → 4 字节大端密钥，与 V_MASK_APP 向量一致
    rx = levels_from_config(RX_SECURITY)["app"]
    assert rx.seed_len == 2 and rx.key_len == 4
    assert rx.compute_key(bytes.fromhex("1234")) == bytes.fromhex("54295DB3")
    assert rx.compute_key(bytes.fromhex("0000")) == bytes.fromhex("F7CBC641")


def test_t1lfl1_crc16_vectors():
    app = levels_from_config(T1LFL1_SECURITY)["app"]
    assert app.seed_len == 2 and app.key_len == 2
    assert app.seed_request() == bytes.fromhex("2703")
    for seed, key in V_T1LFL1:
        assert app.compute_key(bytes.fromhex(seed)) == bytes.fromhex(key), \
            f"seed={seed}"


def test_aes_cmac_seed_magic_optional():
    # V2XEU app：无 magic，种子请求不附加、密钥仍为 CMAC(key, seed)
    app = levels_from_config(V2XEU_SECURITY)["app"]
    assert app.seed_request() == bytes.fromhex("2701")
    assert len(app.compute_key(bytes(16))) == 16
    # magic 给了仍校验长度
    with pytest.raises(ValueError):
        levels_from_config({"app": {"seed_sub": 0x01, "key_sub": 0x02,
                                    "algorithm": "aes_cmac_seed",
                                    "params": {"key": "11" * 16,
                                               "magic": "22" * 15}}})


def test_gac_shift_xor_vectors():
    t75 = levels_from_config(T75_SECURITY)
    app, boot = t75["app"], t75["boot"]
    assert app.seed_len == 4 and app.key_len == 4
    assert app.seed_request() == bytes.fromhex("2701")
    assert boot.seed_request() == bytes.fromhex("2711")
    for seed, key in V_GAC_APP:
        assert app.compute_key(seed.to_bytes(4, "big")) \
            == key.to_bytes(4, "big"), f"seed={seed:08X}"
    for seed, key in V_GAC_BOOT:
        assert boot.compute_key(seed.to_bytes(4, "big")) \
            == key.to_bytes(4, "big"), f"seed={seed:08X}"


def test_seed_request_forms(n60):
    assert n60["app"].seed_request() == bytes.fromhex("2701")
    assert n60["boot"].seed_request() == bytes.fromhex("2711")


def test_app_random_df_deterministic(n60):
    # 随机 DF 生成的 key 应内嵌 DF，且按显式 DF 重算结果一致
    seed = bytes.fromhex(V_APP["seed"])
    key = n60["app"].compute_key(seed)
    assert len(key) == 32
    assert n60["app"].compute_key(seed, key[:16]) == key


def test_seed_length_checked(n60):
    with pytest.raises(ValueError):
        n60["app"].compute_key(bytes(15))
    dev = levels_from_config(None)["develop"]
    with pytest.raises(ValueError):
        dev.compute_key(bytes(17))


# ===== perform_unlock 序列 =====


class SecFake(FakeTransport):
    """模拟 ECU 0x27 服务：收 27 <seed_sub> 回种子，收 27 <key_sub> 按算法校验。"""

    def __init__(self, level, seed, accept=True):
        super().__init__()
        self.level, self.seed, self.accept = level, seed, accept

    def send_payload(self, payload):
        super().send_payload(payload)
        lvl = self.level
        if payload[:2] == bytes([0x27, lvl.seed_sub]):
            self.responses.append(bytes([0x67, lvl.seed_sub]) + self.seed)
        elif payload[:2] == bytes([0x27, lvl.key_sub]):
            key = payload[2:]
            df = key[:16] if len(key) == 32 else None
            ok = key == lvl.compute_key(self.seed, df)
            self.responses.append(
                bytes([0x67, lvl.key_sub]) if ok and self.accept
                else bytes.fromhex("7F2735"))


def test_perform_unlock_app_success(n60):
    seed = bytes.fromhex(V_APP["seed"])
    uds = UdsClient(SecFake(n60["app"], seed))
    seed_out, key, result = perform_unlock(uds, n60["app"])
    assert seed_out == seed
    assert len(key) == 32
    assert result.response == bytes.fromhex("6702")


def test_perform_unlock_develop_success():
    seed = bytes.fromhex(V_DEV["seed"])
    dev = levels_from_config(None)["develop"]
    uds = UdsClient(SecFake(dev, seed))
    seed_out, key, result = perform_unlock(uds, dev)
    assert seed_out == seed
    assert key == bytes.fromhex(V_DEV["key"])
    assert result.response == bytes.fromhex("6762")


def test_perform_unlock_cmc_mask_success():
    rx = levels_from_config(RX_SECURITY)["app"]
    uds = UdsClient(SecFake(rx, bytes.fromhex("1234")))
    seed_out, key, result = perform_unlock(uds, rx)
    assert seed_out == bytes.fromhex("1234")
    assert key == bytes.fromhex("54295DB3")
    assert result.response == bytes.fromhex("6702")


def test_perform_unlock_t1lfl1_success():
    app = levels_from_config(T1LFL1_SECURITY)["app"]
    uds = UdsClient(SecFake(app, bytes.fromhex("1234")))
    seed_out, key, result = perform_unlock(uds, app)
    assert seed_out == bytes.fromhex("1234")
    assert key == bytes.fromhex("017A")
    assert result.response == bytes.fromhex("6704")


def test_perform_unlock_gac_shift_success():
    app = levels_from_config(T75_SECURITY)["app"]
    uds = UdsClient(SecFake(app, (0x12345678).to_bytes(4, "big")))
    seed_out, key, result = perform_unlock(uds, app)
    assert seed_out == (0x12345678).to_bytes(4, "big")
    assert key == bytes.fromhex("B0F0E765")
    assert result.response == bytes.fromhex("6702")


def test_perform_unlock_wrong_key_fails(n60):
    uds = UdsClient(SecFake(n60["app"], bytes(16), accept=False))
    _seed, _key, result = perform_unlock(uds, n60["app"])
    assert result.nrc == 0x35
    assert result.response == bytes.fromhex("7F2735")


def test_perform_unlock_seed_timeout_raises(n60):
    uds = UdsClient(FakeTransport([]), p2_ms=30)
    from security import UnlockError
    with pytest.raises(UnlockError):
        perform_unlock(uds, n60["app"])
