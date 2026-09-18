import pytest

from fake_transport import FakeTransport
from security import compute_key, perform_unlock, seed_request
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


def test_app_key_golden():
    assert compute_key("app", bytes.fromhex(V_APP["seed"]),
                       bytes.fromhex(V_APP["df"])) == bytes.fromhex(V_APP["key"])


def test_boot_key_golden():
    assert compute_key("boot", bytes.fromhex(V_BOOT["seed"]),
                       bytes.fromhex(V_BOOT["df"])) == bytes.fromhex(V_BOOT["key"])


def test_develop_key_golden():
    assert compute_key("develop", bytes.fromhex(V_DEV["seed"])) == bytes.fromhex(V_DEV["key"])


def test_seed_request_forms():
    assert seed_request("app") == bytes.fromhex("2701")
    assert seed_request("boot") == bytes.fromhex("2711")
    # develop 取种子请求携带 16 字节魔数
    assert seed_request("develop") == bytes.fromhex(
        "2761CFBB609DBC8293CCA532D21409774D84")


def test_app_random_df_deterministic():
    # 随机 DF 生成的 key 应内嵌 DF，且按显式 DF 重算结果一致
    seed = bytes.fromhex(V_APP["seed"])
    key = compute_key("app", seed)
    assert len(key) == 32
    assert compute_key("app", seed, key[:16]) == key


def test_unknown_level_rejected():
    with pytest.raises(ValueError):
        compute_key("nonsense", bytes(16))
    with pytest.raises(ValueError):
        seed_request("nonsense")


def test_seed_length_checked():
    with pytest.raises(ValueError):
        compute_key("app", bytes(15))
    with pytest.raises(ValueError):
        compute_key("develop", bytes(17))


class SecFake(FakeTransport):
    """模拟 ECU 0x27 服务：收 27 01 回种子，收 27 02 按算法校验 key。"""

    def __init__(self, level, seed):
        super().__init__()
        from security import LEVELS
        self.level = level
        self.seed = seed
        self.sub = LEVELS[level]

    def send_payload(self, payload):
        super().send_payload(payload)
        sub = self.sub
        if payload[:2] == bytes([0x27, sub.seed_sub]):
            self.responses.append(bytes([0x67, sub.seed_sub]) + self.seed)
        elif payload[:2] == bytes([0x27, sub.key_sub]):
            key = payload[2:]
            ok = key == compute_key(self.level, self.seed, key[:16] if len(key) == 32 else None)
            self.responses.append(bytes([0x67, sub.key_sub]) if ok
                                   else bytes.fromhex("7F2735"))


def test_perform_unlock_app_success():
    seed = bytes.fromhex(V_APP["seed"])
    uds = UdsClient(SecFake("app", seed))
    seed_out, key, result = perform_unlock(uds, "app")
    assert seed_out == seed
    assert len(key) == 32
    assert result.response == bytes.fromhex("6702")


def test_perform_unlock_develop_success():
    seed = bytes.fromhex(V_DEV["seed"])
    uds = UdsClient(SecFake("develop", seed))
    seed_out, key, result = perform_unlock(uds, "develop")
    assert seed_out == seed
    assert key == bytes.fromhex(V_DEV["key"])
    assert result.response == bytes.fromhex("6762")


def test_perform_unlock_wrong_key_fails():
    class WrongKeyFake(SecFake):
        def send_payload(self, payload):
            FakeTransport.send_payload(self, payload)
            if payload[:2] == bytes.fromhex("2701"):
                self.responses.append(bytes.fromhex("6701") + self.seed)
            elif payload[:2] == bytes.fromhex("2702"):
                self.responses.append(bytes.fromhex("7F2735"))  # invalidKey

    uds = UdsClient(WrongKeyFake("app", bytes(16)))
    _seed, _key, result = perform_unlock(uds, "app")
    assert result.nrc == 0x35
    assert result.response == bytes.fromhex("7F2735")


def test_perform_unlock_seed_timeout_raises():
    uds = UdsClient(FakeTransport([]), p2_ms=30)
    from security import UnlockError
    with pytest.raises(UnlockError):
        perform_unlock(uds, "app")


# ===== RX（cpj_cmc）mask_table 算法向量 =====
# 按 cpj_cmc dia_cfg/src/0x27/ov_dia_0x27_security_access.cpp
# seedToKeyApp/seedToKeyBoot 逐行转写计算所得
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


def test_seed_to_key_app_vectors():
    from security import seed_to_key_app
    for seed, key in V_MASK_APP:
        assert seed_to_key_app(seed, MASK_TABLE) == key, f"seed={seed:04X}"


def test_seed_to_key_boot_vectors():
    from security import seed_to_key_boot
    for seed, key in V_MASK_BOOT:
        assert seed_to_key_boot(seed, MASK_TABLE) == key, f"seed={seed:04X}"
