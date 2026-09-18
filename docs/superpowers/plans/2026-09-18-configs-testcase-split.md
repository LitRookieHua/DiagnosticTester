# configs/testcase 分离 + 27 解锁配置化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 tools/diag 的配置与测试用例拆成两个文件/两个目录，0x27 安全解锁改为配置驱动（算法注册表 + develop 内置默认），并完成 12 个存量 yaml 的迁移。

**Architecture:** `configs/<项目>/<项目>.yaml` 只含 can/diagnostic/timing/security；`testcase/<项目>/<流程>.yaml` 只含 steps/on_fail/可选 timing 覆盖。security.py 维护命名算法注册表（4 个算法），等级由 `levels_from_config()` 从配置构建，develop（61/62，公司内部）内置默认可覆盖。

**Tech Stack:** Python 3.10+、PyYAML、cryptography（AES-CMAC）、pytest。

**设计文档:** `docs/superpowers/specs/2026-09-18-configs-testcase-split-design.md`

**约定:** 以下所有命令均在 `Y:\01_Src\tools\diag`（git 仓库根）下执行。

**参考源码（算法移植对照，只读）:**
- N60: `Y:\01_Src\02_Workspace_J6\src\mcu_app_j6\cpj_baic\dia_cfg\var\src\vehicle\n60\0x27\ov_dia_0x27_security_access.cpp`
- RX: `Y:\01_Src\02_Workspace_J6\src\mcu_app_j6\cpj_cmc\dia_cfg\src\0x27\ov_dia_0x27_security_access.cpp`

---

### Task 1: RX mask_table 算法纯函数 + 向量单测

**Files:**
- Modify: `security.py`（文件末尾追加两个纯函数）
- Test: `tests/test_security.py`（文件末尾追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_security.py` 末尾追加（向量值由 cpj_cmc C 源码逐行转写后执行算出，是金标准）：

```python
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
```

同时把文件顶部 import 行改为：

```python
from security import compute_key, perform_unlock, seed_request
```

（保持不变，测试内用局部 import，无需改。）

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_security.py -v -k mask`
Expected: FAIL（ImportError: cannot import name 'seed_to_key_app'）

- [ ] **Step 3: 在 security.py 末尾追加实现**

```python
# ===== RX（cpj_cmc）mask_table 算法：对照 C 源码逐行移植 =====
# 注意 u32 截断语义：中间乘法结果按 uint32 回绕；密钥 4 字节大端发送
# （C 侧按 Key[0]<<24|Key[1]<<16|Key[2]<<8|Key[3] 组装比较）。


def seed_to_key_app(seed_u16: int, mask_table: list[int]) -> int:
    """cpj_cmc seedToKeyApp：mask_index = seed%16，迭代移位乘法后异或。"""
    idx = seed_u16 % 16
    chosen = mask_table[idx]
    t = seed_u16 & 0xFFFF
    if t != 0:
        for _ in range(idx):
            t = ((chosen >> 2) + (t >> 2)) & 0xFFFFFFFF
            t = ((t & 0xFFFF) * (((t >> 16) ^ chosen) & 0xFFFF)) & 0xFFFFFFFF
    else:
        t = (t << 1) & 0xFFFFFFFF
    return (t ^ chosen) & 0xFFFFFFFF


def seed_to_key_boot(seed_u16: int, mask_table: list[int]) -> int:
    """cpj_cmc seedToKeyBoot：middle/end 组合移位后异或。"""
    idx = seed_u16 % 16
    chosen = mask_table[idx]
    t = seed_u16 & 0xFFFF
    if t != 0:
        mid = (t + (chosen >> 1)) & 0xFFFFFFFF
        mid = (mid ^ chosen) & 0xFFFF
        end = ((mid * (chosen & 0xFFFF)) >> 3) & 0xFFFF
        t = ((mid << 8) | ((end & 0xFF00) << 16) | (end & 0xFF)) & 0xFFFFFFFF
    else:
        t = (t << 1) & 0xFFFFFFFF
    return (t ^ chosen) & 0xFFFFFFFF
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_security.py -v`
Expected: 全部 PASS（原有测试不受影响）

- [ ] **Step 5: 提交**

```bash
git add security.py tests/test_security.py
git commit -m "feat(diag): 新增 RX（CMC）0x27 mask_table 算法及向量单测"
```

---

### Task 2: 核心重构——security 注册表化 + flow 配置/用例拆分 + diag CLI

一次性完成三个模块的切换（中间态无法保持 import 完整，故单任务单提交）。

**Files:**
- Rewrite: `security.py`
- Rewrite: `flow.py`
- Rewrite: `diag.py`
- Rewrite: `tests/test_security.py`
- Rewrite: `tests/test_flow_config.py`
- Rewrite: `tests/test_run_flow.py`
- Rewrite: `tests/test_diag_cli.py`

- [ ] **Step 1: 重写 tests/test_security.py**

完整新内容：

```python
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

# N60 项目 security 节（与 configs/N60/n60.yaml 一致）
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_security.py -v`
Expected: FAIL/ERROR（`levels_from_config`、`DEV_MAGIC` 不存在）

- [ ] **Step 3: 重写 security.py**

完整新内容：

```python
"""UDS 0x27 安全解锁：算法注册表、等级构建与完整解锁序列。

算法按项目不同在配置 security 节中选择（algorithm 字段），密钥材料
（key factor / mask table / 魔数）全部来自配置参数；develop（61/62，
公司内部通用）为内置默认，配置可覆盖。

算法对照源码：
- aes_cmac_df_token / aes_cmac_seed — cpj_baic n60 变体
- cmc_mask_app / cmc_mask_boot — cpj_cmc（RX）
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import algorithms

from uds import UdsClient

# 公司内部通用 develop（61/62）参数
DEV_KEY = bytes.fromhex("FF0200020503191039490D98AD52DEFF")
DEV_MAGIC = bytes.fromhex("CFBB609DBC8293CCA532D21409774D84")

LEVEL_FIELDS = {"seed_sub", "key_sub", "algorithm", "params"}


@dataclass(frozen=True)
class SecLevel:
    """一个安全等级：子功能号 + 算法 + 参数。长度由算法固有长度决定。"""

    name: str
    seed_sub: int
    key_sub: int
    seed_len: int
    key_len: int
    algorithm: str
    params: dict

    def seed_request(self) -> bytes:
        req = bytes([0x27, self.seed_sub])
        magic = self.params.get("magic")
        if magic:
            req += magic
        return req

    def compute_key(self, seed: bytes, df: bytes | None = None) -> bytes:
        if len(seed) != self.seed_len:
            raise ValueError(f"{self.name}: 种子长度应为 {self.seed_len}，"
                             f"当前 {len(seed)}")
        return ALGORITHMS[self.algorithm](seed, self.params, df)


def _cmac(key: bytes, data: bytes) -> bytes:
    c = cmac.CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def _key_aes_cmac_df_token(seed: bytes, params: dict, df: bytes | None) -> bytes:
    """N60 app/boot：SK=CMAC(KF,DF)、Key=DF||CMAC(SK,Seed)；DF 缺省随机。"""
    df = os.urandom(16) if df is None else df
    sk = _cmac(params["key_factor"], df)
    return df + _cmac(sk, seed)


def _key_aes_cmac_seed(seed: bytes, params: dict, df: bytes | None) -> bytes:
    """develop：Key=CMAC(key,Seed)。"""
    return _cmac(params["key"], seed)


def seed_to_key_app(seed_u16: int, mask_table: list[int]) -> int:
    """cpj_cmc seedToKeyApp：mask_index = seed%16，迭代移位乘法后异或。"""
    idx = seed_u16 % 16
    chosen = mask_table[idx]
    t = seed_u16 & 0xFFFF
    if t != 0:
        for _ in range(idx):
            t = ((chosen >> 2) + (t >> 2)) & 0xFFFFFFFF
            t = ((t & 0xFFFF) * (((t >> 16) ^ chosen) & 0xFFFF)) & 0xFFFFFFFF
    else:
        t = (t << 1) & 0xFFFFFFFF
    return (t ^ chosen) & 0xFFFFFFFF


def seed_to_key_boot(seed_u16: int, mask_table: list[int]) -> int:
    """cpj_cmc seedToKeyBoot：middle/end 组合移位后异或。"""
    idx = seed_u16 % 16
    chosen = mask_table[idx]
    t = seed_u16 & 0xFFFF
    if t != 0:
        mid = (t + (chosen >> 1)) & 0xFFFFFFFF
        mid = (mid ^ chosen) & 0xFFFF
        end = ((mid * (chosen & 0xFFFF)) >> 3) & 0xFFFF
        t = ((mid << 8) | ((end & 0xFF00) << 16) | (end & 0xFF)) & 0xFFFFFFFF
    else:
        t = (t << 1) & 0xFFFFFFFF
    return (t ^ chosen) & 0xFFFFFFFF


def _key_cmc_mask_app(seed: bytes, params: dict, df: bytes | None) -> bytes:
    """RX app：mask_table 迭代运算，密钥 4 字节大端。"""
    return seed_to_key_app(int.from_bytes(seed, "big"),
                           params["mask_table"]).to_bytes(4, "big")


def _key_cmc_mask_boot(seed: bytes, params: dict, df: bytes | None) -> bytes:
    """RX boot：密钥 4 字节大端。"""
    return seed_to_key_boot(int.from_bytes(seed, "big"),
                            params["mask_table"]).to_bytes(4, "big")


# 算法注册表：计算函数 + 固有 (seed_len, key_len) + params 键 → hex 字节数
# （mask_table 特殊：16 项 u32 列表）
ALGORITHMS: dict[str, dict] = {
    "aes_cmac_df_token": {"key": _key_aes_cmac_df_token,
                          "seed_len": 16, "key_len": 32,
                          "params": {"key_factor": 16}},
    "aes_cmac_seed": {"key": _key_aes_cmac_seed, "seed_len": 16, "key_len": 16,
                      "params": {"key": 16, "magic": 16}},
    "cmc_mask_app": {"key": _key_cmc_mask_app, "seed_len": 2, "key_len": 4,
                     "params": {"mask_table": 16}},
    "cmc_mask_boot": {"key": _key_cmc_mask_boot, "seed_len": 2, "key_len": 4,
                      "params": {"mask_table": 16}},
}


def _hex(value, n: int, where: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{where}: 应为 {n} 字节十六进制字符串，"
                         f"当前是 {type(value).__name__}: {value!r}")
    try:
        b = bytes.fromhex(value.replace(" ", ""))
    except ValueError:
        raise ValueError(f"{where}: '{value}' 不是合法十六进制")
    if len(b) != n:
        raise ValueError(f"{where}: 应为 {n} 字节，当前 {len(b)}")
    return b


def _u32(value, where: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{where}: 应为 u32")
    if isinstance(value, int):
        i = value
    elif isinstance(value, str):
        try:
            i = int(value.replace(" ", ""), 16)
        except ValueError:
            raise ValueError(f"{where}: '{value}' 不是合法十六进制 u32")
    else:
        raise ValueError(f"{where}: 应为 u32（int 或十六进制字符串）")
    if not 0 <= i <= 0xFFFFFFFF:
        raise ValueError(f"{where}: 超出 u32 范围")
    return i


def _build_level(name: str, item: dict) -> SecLevel:
    unknown = sorted(set(item) - LEVEL_FIELDS)
    if unknown:
        raise ValueError(f"{name}.{unknown[0]}: 未知字段")
    for k in ("seed_sub", "key_sub", "algorithm"):
        if k not in item:
            raise ValueError(f"{name}.{k}: 缺失")
    seed_sub, key_sub = item["seed_sub"], item["key_sub"]
    for k, v in (("seed_sub", seed_sub), ("key_sub", key_sub)):
        if isinstance(v, bool) or not isinstance(v, int) \
                or not 0x01 <= v <= 0x7E:
            raise ValueError(f"{name}.{k}: 应为 0x01-0x7E 的整数，当前 {v!r}")
    if key_sub != seed_sub + 1:
        raise ValueError(f"{name}.key_sub: 必须为 seed_sub+1"
                         f"（{seed_sub:#04x}→{seed_sub + 1:#04x}），"
                         f"当前 {key_sub!r}")
    algo = item["algorithm"]
    if algo not in ALGORITHMS:
        raise ValueError(f"{name}.algorithm: 只能是 {sorted(ALGORITHMS)}")
    spec = ALGORITHMS[algo]
    raw_params = item.get("params", {})
    if not isinstance(raw_params, dict):
        raise ValueError(f"{name}.params: 必须是映射")
    unknown = sorted(set(raw_params) - set(spec["params"]))
    if unknown:
        raise ValueError(f"{name}.params.{unknown[0]}: 未知参数")
    params = {}
    for k, n in spec["params"].items():
        if k not in raw_params:
            raise ValueError(f"{name}.params.{k}: 缺失")
        if k == "mask_table":
            tbl = raw_params[k]
            if not isinstance(tbl, list) or len(tbl) != n:
                raise ValueError(f"{name}.params.mask_table: 应为 {n} 项列表")
            params[k] = [_u32(v, f"{name}.params.mask_table[{i}]")
                         for i, v in enumerate(tbl)]
        else:
            params[k] = _hex(raw_params[k], n, f"{name}.params.{k}")
    return SecLevel(name=name, seed_sub=seed_sub, key_sub=key_sub,
                    seed_len=spec["seed_len"], key_len=spec["key_len"],
                    algorithm=algo, params=params)


def levels_from_config(raw) -> dict[str, SecLevel]:
    """配置 security 节 → {等级名: SecLevel}；develop 缺省内置，可覆盖。"""
    levels: dict[str, SecLevel] = {}
    if raw is not None:
        if not isinstance(raw, dict):
            raise ValueError("必须是映射（等级名 → 等级定义）")
        for name, item in raw.items():
            if not isinstance(item, dict):
                raise ValueError(f"{name}: 必须是映射")
            levels[str(name)] = _build_level(str(name), item)
    if "develop" not in levels:
        levels["develop"] = _build_level("develop", {
            "seed_sub": 0x61, "key_sub": 0x62, "algorithm": "aes_cmac_seed",
            "params": {"key": DEV_KEY.hex().upper(),
                       "magic": DEV_MAGIC.hex().upper()}})
    return levels


class UnlockError(Exception):
    """解锁序列在种子阶段失败（超时/负响应/响应格式异常）。timeout 标记失败原因。"""

    def __init__(self, msg, timeout: bool = False):
        super().__init__(msg)
        self.timeout = timeout


def perform_unlock(uds: UdsClient, level: SecLevel, timeout_ms: int | None = None,
                   log=None):
    """执行完整 0x27 序列：取种子 → 计算密钥 → 发密钥。

    返回 (seed, key, result)；result 为密钥请求的 UdsResult（response
    以 67+key_sub 开头即成功）。种子阶段失败抛 UnlockError。
    """
    log = log or (lambda *a, **k: None)

    r = uds.request(level.seed_request(), timeout_ms=timeout_ms)
    if r.timeout:
        raise UnlockError(f"取种子超时（27 {level.seed_sub:02X}）", timeout=True)
    if r.nrc is not None:
        raise UnlockError(f"取种子负响应 NRC 0x{r.nrc:02X}")
    expect = bytes([0x67, level.seed_sub])
    if r.response is None or not r.response.startswith(expect) \
            or len(r.response) < len(expect) + level.seed_len:
        raise UnlockError(f"取种子响应异常："
                          f"{r.response.hex().upper() if r.response else '无'}")
    seed = r.response[len(expect):len(expect) + level.seed_len]
    log(f"安全解锁[{level.name}] 种子 {seed.hex().upper()}")

    key = level.compute_key(seed)
    log(f"安全解锁[{level.name}] 密钥 {key.hex().upper()}")
    result = uds.request(bytes([0x27, level.key_sub]) + key,
                         timeout_ms=timeout_ms)
    return seed, key, result
```

- [ ] **Step 4: 运行 security 测试确认通过**

Run: `python -m pytest tests/test_security.py -v`
Expected: 全部 PASS（其余测试文件此时仍 FAIL，属预期）

- [ ] **Step 5: 重写 tests/test_flow_config.py**

完整新内容：

```python
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
    _cfg, tc = load(tmp_path, tc_text=text)
    assert tc["steps"][1].request is None


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
```

注意：`test_missing_step_field` 期望步骤缺 request 时报错——上面写法有误，修正为：

```python
def test_missing_step_field(tmp_path):
    text = STEPS.replace('  - {name: 读DID, request: "22F195", expect: "62F195"}',
                         '  - {name: 读DID}')
    with pytest.raises(ConfigError) as e:
        load(tmp_path, tc_text=text)
    assert "steps[1].request" in str(e.value)
```

- [ ] **Step 6: 运行确认失败**

Run: `python -m pytest tests/test_flow_config.py -v`
Expected: ERROR（`load_testcase` 不存在）

- [ ] **Step 7: 重写 flow.py**

完整新内容：

```python
"""配置与测试用例：加载、校验、流程执行。

配置（configs/<项目>/<项目>.yaml）：can / diagnostic / timing / security，
由 load_config 加载；测试用例（testcase/<项目>/<流程>.yaml）：
steps / on_fail / 可选 timing 覆盖，由 load_testcase(path, cfg) 加载。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from can_driver import CanFrame
from isotp import IsoTpTransport, encode
from recorder import Recorder
from security import UnlockError, levels_from_config, perform_unlock
from uds import UdsClient, UdsResult

HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
STEP_FIELDS = {"name", "request", "expect", "timeout_ms", "delay_after_ms",
               "addressing", "security_access"}
CAN_FIELDS = {"device", "channel", "fd", "baudrate", "data_baudrate"}
DIAGNOSTIC_FIELDS = {"request_id", "response_id", "functional_id"}
TIMING_FIELDS = {"p2_ms", "p2star_ms", "step_delay_ms"}
CONFIG_FIELDS = {"can", "diagnostic", "timing", "security"}
TESTCASE_FIELDS = {"steps", "on_fail", "timing"}


class ConfigError(Exception):
    pass


@dataclass
class Step:
    name: str
    request: bytes | None = None
    expect: bytes | None = None
    timeout_ms: int | None = None
    delay_after_ms: int | None = None
    addressing: str = "physical"
    security_access: str | None = None


def parse_hex_bytes(s, where: str) -> bytes:
    if not isinstance(s, str):
        raise ConfigError(f"{where}: 应为引号包裹的十六进制字符串（如 \"1003\"），"
                          f"当前是 {type(s).__name__}: {s!r}")
    s = s.replace(" ", "")
    if not HEX_RE.match(s) or len(s) % 2:
        raise ConfigError(f"{where}: '{s}' 不是合法十六进制字节串")
    return bytes.fromhex(s)


def parse_id(v, where: str) -> int:
    if v is None:
        raise ConfigError(f"{where}: 缺失")
    if isinstance(v, bool):
        raise ConfigError(f"{where}: 应为 CAN ID")
    if isinstance(v, int):
        i = v
    else:
        s = str(v).strip().lower().replace("0x", "")
        try:
            i = int(s, 16)
        except ValueError:
            raise ConfigError(f"{where}: '{v}' 不是合法 CAN ID")
    if not 0x001 <= i <= 0x7FF:
        raise ConfigError(f"{where}: CAN ID 超出 11 位标准帧范围 0x001-0x7FF：0x{i:X}")
    return i


def _reject_unknown_keys(section: dict, allowed: set, section_name: str):
    """嵌套节未知键校验（与 steps 的未知字段校验同款）：拼错的键必须报错，
    不能静默忽略后回落默认值。"""
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigError("、".join(f"{section_name}.{k}" for k in unknown) + ": 未知字段")


def _check_num(d, k, path, min_=0):
    v = d.get(k)
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{path}.{k}: 应为数字，当前是 {type(v).__name__}: {v!r}")
    if v < min_:
        raise ConfigError(f"{path}.{k}: 不能小于 {min_}，当前 {v}")


def _load_yaml(path, what: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{what}不存在：{path}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 语法错误：{e}")
    if not isinstance(raw, dict):
        raise ConfigError("根节点必须是映射")
    return raw


def _validate_timing(raw, base: dict | None = None) -> dict:
    """校验 timing 节并补默认值；给 base 时为覆盖合并（缺省键继承 base）。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("timing: 必须是映射")
    _reject_unknown_keys(raw, TIMING_FIELDS, "timing")
    for k in ("p2_ms", "p2star_ms", "step_delay_ms"):
        _check_num(raw, k, "timing")
    timing = (dict(base) if base is not None
              else {"p2_ms": 50, "p2star_ms": 5000, "step_delay_ms": 100})
    timing.update({k: v for k, v in raw.items() if v is not None})
    return timing


def load_config(path) -> dict:
    """加载并校验项目配置（can/diagnostic/timing/security）。"""
    raw = _load_yaml(path, "配置文件")
    unknown = set(raw) - CONFIG_FIELDS
    if unknown:
        raise ConfigError(f"未知顶层字段 {sorted(unknown)}")

    can = raw.get("can")
    if not isinstance(can, dict):
        raise ConfigError("can: 缺失或不是映射")
    _reject_unknown_keys(can, CAN_FIELDS, "can")
    for k in ("device", "channel", "fd", "baudrate"):
        if k not in can:
            raise ConfigError(f"can.{k}: 缺失")
    if not isinstance(can["fd"], bool):
        raise ConfigError("can.fd: 应为 true/false")
    can.setdefault("data_baudrate", 2000000)
    for k in ("channel", "baudrate", "data_baudrate"):
        _check_num(can, k, "can")

    diag = raw.get("diagnostic")
    if not isinstance(diag, dict):
        raise ConfigError("diagnostic: 缺失或不是映射")
    _reject_unknown_keys(diag, DIAGNOSTIC_FIELDS, "diagnostic")
    for k in ("request_id", "response_id"):
        if k not in diag:
            raise ConfigError(f"diagnostic.{k}: 缺失")
        diag[k] = parse_id(diag[k], f"diagnostic.{k}")
    if diag.get("functional_id") is not None:
        diag["functional_id"] = parse_id(diag["functional_id"],
                                         "diagnostic.functional_id")

    timing = _validate_timing(raw.get("timing"))

    try:
        security_levels = levels_from_config(raw.get("security"))
    except ValueError as e:
        raise ConfigError(f"security: {e}")

    return {"can": can, "diagnostic": diag, "timing": timing,
            "security_levels": security_levels}


def _parse_steps(raw_steps, cfg: dict) -> list[Step]:
    can, diag = cfg["can"], cfg["diagnostic"]
    levels = cfg["security_levels"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ConfigError("steps: 必须是非空列表")
    steps = []
    for i, s in enumerate(raw_steps):
        where = f"steps[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{where}: 必须是映射")
        unknown = set(s) - STEP_FIELDS
        if unknown:
            raise ConfigError(f"{where}: 未知字段 {sorted(unknown)}")
        if "name" not in s:
            raise ConfigError(f"{where}.name: 缺失")
        for k in ("timeout_ms", "delay_after_ms"):
            _check_num(s, k, where)
        security = s.get("security_access")
        if security is not None:
            if "request" in s or "expect" in s:
                raise ConfigError(f"{where}: security_access 步骤不支持 request/expect")
            if security not in levels:
                raise ConfigError(f"{where}.security_access: 只能是 {sorted(levels)}")
            if s.get("addressing", "physical") != "physical":
                raise ConfigError(f"{where}: 安全解锁只支持物理寻址 physical")
            steps.append(Step(name=str(s["name"]), security_access=security,
                              timeout_ms=s.get("timeout_ms"),
                              delay_after_ms=s.get("delay_after_ms")))
            continue
        if "request" not in s:
            raise ConfigError(f"{where}.request: 缺失")
        request = parse_hex_bytes(s["request"], f"{where}.request")
        expect = (parse_hex_bytes(s["expect"], f"{where}.expect")
                  if s.get("expect") is not None else None)
        addressing = s.get("addressing", "physical")
        if addressing not in ("physical", "functional"):
            raise ConfigError(f"{where}.addressing: 只能是 physical 或 functional")
        if addressing == "functional":
            if expect is not None:
                raise ConfigError(f"{where}: 功能寻址步骤不支持 expect（发完即记，不做判定）")
            if diag.get("functional_id") is None:
                raise ConfigError(f"{where}: 功能寻址需要 diagnostic.functional_id")
            sf_max = 62 if can["fd"] else 7
            if len(request) > sf_max:
                raise ConfigError(f"{where}: 功能寻址请求超过单帧上限 {sf_max} 字节")
        steps.append(Step(name=str(s["name"]), request=request, expect=expect,
                          timeout_ms=s.get("timeout_ms"),
                          delay_after_ms=s.get("delay_after_ms"),
                          addressing=addressing))
    return steps


def load_testcase(path, cfg: dict) -> dict:
    """加载并校验测试用例（steps/on_fail/可选 timing 覆盖）。"""
    raw = _load_yaml(path, "测试用例文件")
    unknown = set(raw) - TESTCASE_FIELDS
    if unknown:
        raise ConfigError(f"未知顶层字段 {sorted(unknown)}")
    timing = _validate_timing(raw.get("timing"), base=cfg["timing"])
    on_fail = raw.get("on_fail", "abort")
    if on_fail not in ("abort", "continue"):
        raise ConfigError("on_fail: 只能是 abort 或 continue")
    steps = _parse_steps(raw.get("steps"), cfg)
    return {"steps": steps, "on_fail": on_fail, "timing": timing}


def judge(step: Step, r: UdsResult) -> str:
    if step.expect is None:
        return "PASS"  # 无期望即记录：只发不判，超时不算失败
    if r.timeout:
        return "TIMEOUT"
    if r.response is not None and r.response.startswith(step.expect):
        return "PASS"
    return "FAIL"


def run_flow(cfg: dict, testcase: dict, driver, run_dir):
    """执行流程，返回汇总 dict（total/pass/fail/timeout）。driver 为已就绪的 CanDriver。"""
    diag = cfg["diagnostic"]
    timing = testcase["timing"]
    steps = testcase["steps"]
    on_fail = testcase["on_fail"]
    levels = cfg["security_levels"]

    def delay_s(step) -> float:
        ms = step.delay_after_ms if step.delay_after_ms is not None else timing["step_delay_ms"]
        return ms / 1000.0

    counters = {"total": 0, "pass": 0, "fail": 0, "timeout": 0}
    with Recorder(run_dir) as rec:
        def tap(frame, is_rx):
            rec.frame(frame, is_rx)

        def log(msg):
            rec.event(msg)

        transport = IsoTpTransport(driver, tx_id=diag["request_id"],
                                   rx_id=diag["response_id"],
                                   is_fd=cfg["can"]["fd"], tap=tap, log=log)
        transport.start()
        try:
            uds = UdsClient(transport, p2_ms=timing["p2_ms"],
                            p2star_ms=timing["p2star_ms"], log=log)
            rec.event(f"流程开始：共 {len(steps)} 步，on_fail={on_fail}")
            for i, step in enumerate(steps, 1):
                counters["total"] += 1
                if step.security_access is not None:
                    lvl = levels[step.security_access]
                    log(f"[{i}] {step.name} 安全解锁（{step.security_access} 级）")
                    try:
                        seed, key, result = perform_unlock(
                            uds, lvl, timeout_ms=step.timeout_ms, log=log)
                    except UnlockError as e:
                        result, verdict = UdsResult(), "FAIL"
                        log(f"[{i}] {step.name} -> {e} 判定 FAIL")
                    else:
                        ok = (result.response is not None
                              and result.response.startswith(
                                  bytes([0x67, lvl.key_sub])))
                        verdict = ("PASS" if ok
                                   else "TIMEOUT" if result.timeout else "FAIL")
                        log(f"[{i}] {step.name} -> "
                            f"{result.response.hex().upper() if result.response else '无响应'}"
                            f" 判定 {verdict}"
                            + (f"（NRC 0x{result.nrc:02X}）" if result.nrc is not None else ""))
                    rec.step(i, step.name, "physical",
                             f"27{lvl.seed_sub:02X}+27{lvl.key_sub:02X}", result, verdict)
                elif step.addressing == "functional":
                    frames = encode(step.request, cfg["can"]["fd"])
                    for f in frames:
                        frame = CanFrame(diag["functional_id"], f,
                                         is_fd=cfg["can"]["fd"],
                                         bitrate_switch=cfg["can"]["fd"])
                        driver.send(frame)
                        rec.frame(frame, False)
                    rec.event(f"[{i}] {step.name} 功能寻址发送 "
                              f"{step.request.hex().upper()}（不等待响应）")
                    # 发完即记一行 SENT：CSV 行数与已执行步骤对齐、与 abort 跳过可区分
                    rec.step(i, step.name, step.addressing,
                             step.request.hex().upper(), UdsResult(), "SENT")
                    counters["pass"] += 1
                    time.sleep(delay_s(step))
                    continue

                else:
                    log(f"[{i}] {step.name} 发送 {step.request.hex().upper()}"
                        f"（期望 {step.expect.hex().upper() if step.expect else '无'}）")
                    result = uds.request(step.request, timeout_ms=step.timeout_ms)
                    verdict = judge(step, result)
                    rec.step(i, step.name, step.addressing,
                             step.request.hex().upper(), result, verdict)
                    log(f"[{i}] {step.name} -> "
                        f"{result.response.hex().upper() if result.response else '无响应'}"
                        f" 判定 {verdict}"
                        + (f"（NRC 0x{result.nrc:02X}）" if result.nrc is not None else ""))
                if verdict == "PASS":
                    counters["pass"] += 1
                elif verdict == "FAIL":
                    counters["fail"] += 1
                else:
                    counters["timeout"] += 1
                if verdict != "PASS" and on_fail == "abort":
                    log("步骤失败且 on_fail=abort，中止流程")
                    break
                time.sleep(delay_s(step))
        finally:
            transport.stop()
            rec.event(f"流程结束：PASS {counters['pass']} / FAIL {counters['fail']}"
                      f" / TIMEOUT {counters['timeout']}"
                      f" / 共 {counters['total']}/{len(steps)}")
    return counters
```

- [ ] **Step 8: 运行 flow 配置测试确认通过**

Run: `python -m pytest tests/test_flow_config.py -v`
Expected: 全部 PASS

- [ ] **Step 9: 重写 tests/test_run_flow.py**

完整新内容：

```python
import pytest

from flow import load_config, load_testcase, run_flow
from mock_driver import MockCanDriver
from security import levels_from_config

CONFIG = """
can: {device: mock, channel: 0, fd: false, baudrate: 500000}
diagnostic: {request_id: 0x70C, response_id: 0x78C, functional_id: 0x7DF}
timing: {p2_ms: 100, p2star_ms: 1000, step_delay_ms: 0}
"""

STEPS = """
steps:
  - {name: s1, request: "1003", expect: "5003"}
  - {name: s2, request: "22F195", expect: "62F195"}
on_fail: abort
"""

SEC_CONFIG = CONFIG + """
security:
  app: {seed_sub: 0x01, key_sub: 0x02, algorithm: aes_cmac_df_token,
        params: {key_factor: "5E4741955FEF78D046D754F3CDEF170B"}}
"""

SEC_STEPS = """
steps:
  - {name: 解锁, security_access: app}
on_fail: abort
"""


def load(tmp_path, cfg_text=CONFIG, tc_text=STEPS):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    tc_path = tmp_path / "tc.yaml"
    tc_path.write_text(tc_text, encoding="utf-8")
    cfg = load_config(cfg_path)
    return cfg, load_testcase(tc_path, cfg)


def test_run_flow_pass(tmp_path):
    cfg, tc = load(tmp_path)

    mock = MockCanDriver()

    def on_send(frame):
        d = frame.data
        req = d[1:1 + d[0]]  # 按SF PCI长度截取（帧补齐到8字节）
        if req == bytes.fromhex("1003"):
            mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
        elif req == bytes.fromhex("22F195"):
            mock.push_rx(0x78C, bytes([5]) + bytes.fromhex("62F1950102"))

    mock.on_send = on_send
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 2, "pass": 2, "fail": 0, "timeout": 0}
    assert (tmp_path / "run.log").exists()
    assert (tmp_path / "result.csv").exists()
    assert (tmp_path / "trace.blf").exists()


def test_on_fail_abort_stops(tmp_path):
    # s1 期望改成不匹配的 62xx → FAIL → abort，s2 不再执行
    tc_text = STEPS.replace('request: "1003", expect: "5003"',
                            'request: "1003", expect: "62"')
    cfg, tc = load(tmp_path, tc_text=tc_text)
    mock = MockCanDriver()
    mock.on_send = lambda f: mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary["total"] == 1
    assert summary["fail"] == 1
    assert len(mock.sent) == 1


def test_on_fail_continue_runs_all(tmp_path):
    tc_text = STEPS.replace('request: "1003", expect: "5003"',
                            'request: "1003", expect: "62"')
    tc_text = tc_text.replace("on_fail: abort", "on_fail: continue")
    cfg, tc = load(tmp_path, tc_text=tc_text)
    mock = MockCanDriver()
    mock.on_send = lambda f: mock.push_rx(0x78C, bytes([4]) + bytes.fromhex("50030032"))
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary["total"] == 2
    assert summary["fail"] == 2


def test_timing_override_from_testcase(tmp_path):
    # 用例覆盖 p2_ms=1：无响应时应快速超时判 TIMEOUT（而非等配置的 100ms）
    tc_text = "timing: {p2_ms: 1}\n" + STEPS.replace(
        '  - {name: s2, request: "22F195", expect: "62F195"}', "")
    cfg, tc = load(tmp_path, tc_text=tc_text)
    mock = MockCanDriver()  # 不回帧
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 0, "timeout": 1}


def test_functional_step_no_wait(tmp_path):
    # 两步都是功能寻址：直接发送、不等待响应，应全 PASS
    tc_text = STEPS.replace(
        '  - {name: s1, request: "1003", expect: "5003"}\n'
        '  - {name: s2, request: "22F195", expect: "62F195"}',
        '  - {name: s1, request: "1003", addressing: functional}\n'
        '  - {name: s2, request: "3E80", addressing: functional}')
    cfg, tc = load(tmp_path, tc_text=tc_text)
    mock = MockCanDriver()
    summary = run_flow(cfg, tc, mock, tmp_path)
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
    cfg, tc = load(tmp_path)
    mock = MockCanDriver()

    def on_send(frame):
        raise RuntimeError("boom")

    mock.on_send = on_send
    with pytest.raises(RuntimeError):
        run_flow(cfg, tc, mock, tmp_path)
    blf = tmp_path / "trace.blf"
    assert blf.exists() and blf.stat().st_size > 0
    assert (tmp_path / "run.log").exists()


def test_timeout_triggers_abort(tmp_path):
    # 无响应且带 expect：TIMEOUT 也应中止后续步骤（on_fail=abort）并记入 CSV
    tc_text = STEPS.replace(
        '  - {name: s1, request: "1003", expect: "5003"}\n'
        '  - {name: s2, request: "22F195", expect: "62F195"}',
        '  - {name: s1, request: "1003", expect: "5003"}')
    cfg, tc = load(tmp_path, tc_text=tc_text)
    mock = MockCanDriver()  # 不回帧
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 0, "timeout": 1}
    assert "TIMEOUT" in (tmp_path / "result.csv").read_text(encoding="utf-8")


def test_nrc_recorded_in_csv(tmp_path):
    # 负响应的 NRC 应记入 CSV 的 nrc 列，且判 FAIL
    tc_text = STEPS.replace(
        '  - {name: s1, request: "1003", expect: "5003"}\n'
        '  - {name: s2, request: "22F195", expect: "62F195"}',
        '  - {name: s1, request: "22F195", expect: "62F195"}')
    cfg, tc = load(tmp_path, tc_text=tc_text)
    mock = MockCanDriver()
    mock.on_send = lambda f: mock.push_rx(0x78C, bytes([3]) + bytes.fromhex("7F2231"))
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 1, "timeout": 0}
    csv_text = (tmp_path / "result.csv").read_text(encoding="utf-8")
    assert "0x31" in csv_text
    assert "FAIL" in csv_text


SEED = bytes.fromhex("2CBA4F594105BBF56B52B17757369A46")

APP_LEVEL = levels_from_config({
    "app": {"seed_sub": 0x01, "key_sub": 0x02,
            "algorithm": "aes_cmac_df_token",
            "params": {"key_factor": "5E4741955FEF78D046D754F3CDEF170B"}}
})["app"]


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
    """模拟 ECU 0x27：回固定种子，按算法校验密钥。accept=False 时密钥恒错。"""

    def __init__(self, mock, seed, accept=True, level=APP_LEVEL):
        self.mock, self.seed, self.accept, self.level = mock, seed, accept, level

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


def test_security_step_unlock_pass(tmp_path):
    cfg, tc = load(tmp_path, cfg_text=SEC_CONFIG, tc_text=SEC_STEPS)
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED)
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 1, "pass": 1, "fail": 0, "timeout": 0}
    # 密钥请求应为 34B payload 的 FD 转义 SF：00 22 27 02 ...
    key_frame = mock.sent[1]
    assert key_frame.data[:3] == bytes([0x00, 34, 0x27])
    csv_text = (tmp_path / "result.csv").read_text(encoding="utf-8")
    assert "PASS" in csv_text and "2701+2702" in csv_text
    log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert SEED.hex().upper() in log_text  # 种子记入日志


def test_security_step_wrong_key_fail(tmp_path):
    cfg, tc = load(tmp_path, cfg_text=SEC_CONFIG, tc_text=SEC_STEPS)
    mock = MockCanDriver()
    mock.on_send = SecEcu(mock, SEED, accept=False)
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 1, "timeout": 0}
    assert "FAIL" in (tmp_path / "result.csv").read_text(encoding="utf-8")


def test_security_step_seed_timeout_fail(tmp_path):
    # 取种子无响应：UnlockError → 判 FAIL（abort 中止流程）
    cfg, tc = load(tmp_path, cfg_text=SEC_CONFIG, tc_text=SEC_STEPS)
    mock = MockCanDriver()  # 不回帧
    summary = run_flow(cfg, tc, mock, tmp_path)
    assert summary == {"total": 1, "pass": 0, "fail": 1, "timeout": 0}
    assert "FAIL" in (tmp_path / "result.csv").read_text(encoding="utf-8")
```

- [ ] **Step 10: 运行确认通过**

Run: `python -m pytest tests/test_run_flow.py -v`
Expected: 全部 PASS

- [ ] **Step 11: 重写 tests/test_diag_cli.py**

完整新内容：

```python
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

    def runner(argv, ecu=None, cfg_text=CONFIG, tc_text=TESTCASE):
        cfg_path = tmp_path / "cfg.yaml"
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


def test_run_log_dir_uses_testcase_stem(run_cli, tmp_path):
    # 日志目录前缀取 testcase 文件名，而非 config
    mock = MockCanDriver()
    run_cli(["run", "tc"], ecu=mock)  # 无响应超时也无妨，目录已建
    assert any(p.is_dir() and p.name.startswith("tc_")
               for p in tmp_path.iterdir())


def test_run_missing_config_rejected(run_cli):
    code, out = run_cli(["run"])  # 缺 --config：argparse 报错
    assert code != 0


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
    # 等级来自配置：未知等级报配置错误（退出码 2），无需硬件
    code, out = run_cli(["unlock", "ota"])
    assert code == 2
    assert "ota" in out


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
```

- [ ] **Step 12: 运行确认失败**

Run: `python -m pytest tests/test_diag_cli.py -v`
Expected: FAIL（diag.py 尚未改造：run 缺 --config、load_config 不接受新格式等）

- [ ] **Step 13: 重写 diag.py**

完整新内容：

```python
"""诊断测试工具 CLI。

用法：
  python diag.py run <testcase.yaml> --config <config.yaml>   跑测试用例
  python diag.py chat --config <config.yaml>                  交互模式
  python diag.py probe --config <config.yaml> 22F195          单发一条请求
  python diag.py unlock --config <config.yaml> app             0x27 安全解锁
"""
from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from flow import ConfigError, load_config, load_testcase, parse_hex_bytes
from isotp import IsoTpError, IsoTpTransport
from recorder import Recorder
from security import UnlockError, perform_unlock
from uds import UdsClient

TOOLS_DIR = Path(__file__).resolve().parent
LOGS_DIR = TOOLS_DIR / "logs"


def make_driver(cfg):
    can_cfg = cfg["can"]
    if can_cfg["device"] == "mock":
        tests_dir = str(TOOLS_DIR / "tests")
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from mock_driver import MockCanDriver
        return MockCanDriver()
    from can_driver import ZlgCanDriver
    return ZlgCanDriver(device=can_cfg["device"], channel=can_cfg["channel"],
                        baudrate=can_cfg["baudrate"],
                        data_baudrate=can_cfg.get("data_baudrate", 2000000),
                        fd=can_cfg["fd"])


def new_run_dir(prefix: str) -> Path:
    """按时间戳建运行目录；同秒重复运行追加 _1/_2… 后缀，避免互相覆盖。"""
    base = LOGS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    for i in range(100):
        d = base if i == 0 else base.with_name(f"{base.name}_{i}")
        try:
            d.mkdir(parents=True, exist_ok=False)
            return d
        except FileExistsError:
            continue
    raise RuntimeError(f"运行目录碰撞超过 100 个候选：{base}")


def cmd_run(args):
    cfg = load_config(args.config)
    tc = load_testcase(args.testcase, cfg)
    driver = make_driver(cfg)
    run_dir = new_run_dir(Path(args.testcase).stem)
    print(f"运行目录：{run_dir}")
    try:
        counters = run_flow(cfg, tc, driver, run_dir)
    finally:
        driver.close()
    print(f"PASS {counters['pass']} / FAIL {counters['fail']} "
          f"/ TIMEOUT {counters['timeout']} / 共 {counters['total']}/{len(tc['steps'])}")
    return 0 if counters["fail"] == 0 and counters["timeout"] == 0 else 1


def build_transport(cfg, driver, rec):
    diag = cfg["diagnostic"]
    t = IsoTpTransport(driver, tx_id=diag["request_id"], rx_id=diag["response_id"],
                       is_fd=cfg["can"]["fd"],
                       tap=lambda f, rx: rec.frame(f, rx), log=rec.event)
    t.start()
    return t


@contextmanager
def open_session(cfg, prefix):
    """probe/chat/unlock 共用的会话资源：driver → 记录器 → 传输 → UDS 客户端，退出时逆序收尾。

    run_flow 自管 transport（与步骤执行耦合），cmd_run 不走此入口。
    """
    driver = make_driver(cfg)
    rec = Recorder(new_run_dir(prefix))
    transport = None
    try:
        transport = build_transport(cfg, driver, rec)
        uds = UdsClient(transport, p2_ms=cfg["timing"]["p2_ms"],
                        p2star_ms=cfg["timing"]["p2star_ms"], log=rec.event)
        yield driver, rec, transport, uds
    finally:
        if transport is not None:
            transport.stop()
        rec.close()
        driver.close()


def cmd_probe(args):
    cfg = load_config(args.config)
    request = parse_hex_bytes(args.request, "request")
    with open_session(cfg, "probe") as (_driver, _rec, _transport, uds):
        r = uds.request(request)
        print(f"响应：{r.response.hex().upper() if r.response else '超时无响应'}"
              + (f"（NRC 0x{r.nrc:02X}）" if r.nrc is not None else ""))
        if r.timeout:
            return 1  # ECU 无响应（P2 超时）
        if r.nrc is not None:
            return 2  # 收到负响应
        return 0


def cmd_unlock(args):
    cfg = load_config(args.config)
    levels = cfg["security_levels"]
    if args.level not in levels:
        print(f"未知安全等级 {args.level!r}，可选 {sorted(levels)}", file=sys.stderr)
        return 2
    with open_session(cfg, "unlock") as (_driver, rec, _transport, uds):
        log = rec.event
        level = levels[args.level]
        log(f"安全解锁（{args.level} 级）")
        try:
            seed, key, result = perform_unlock(uds, level, log=log)
        except UnlockError as e:
            log(f"安全解锁[{args.level}] 失败：{e}")
            print(f"解锁失败：{e}")
            return 1 if e.timeout else 2
        print(f"种子：{seed.hex().upper()}")
        print(f"密钥：{key.hex().upper()}")
        print(f"响应：{result.response.hex().upper() if result.response else '超时无响应'}"
              + (f"（NRC 0x{result.nrc:02X}）" if result.nrc is not None else ""))
        if result.timeout:
            return 1  # ECU 无响应（P2 超时）
        if result.nrc is not None:
            return 2  # 收到负响应
        return 0


def cmd_chat(args):
    cfg = load_config(args.config)
    levels = cfg["security_levels"]
    with open_session(cfg, "chat") as (_driver, rec, _transport, uds):
        print(f"交互模式（日志：{rec.run_dir}）。输入十六进制请求如 22F195，"
              f"unlock <{'/'.join(sorted(levels))}> 解锁，:q 退出")
        while True:
            try:
                line = input("diag> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in (":q", ":quit", "exit"):
                break
            parts = line.split()
            if parts[0].lower() == "unlock":
                level = parts[1] if len(parts) > 1 else ""
                if level not in levels:
                    print(f"未知等级 {level!r}，可选 {sorted(levels)}")
                    continue
                rec.event(f"手动解锁（{level} 级）")
                try:
                    seed, key, result = perform_unlock(uds, levels[level],
                                                       log=rec.event)
                except UnlockError as e:
                    print(f"解锁失败：{e}")
                    continue
                except (TimeoutError, IsoTpError, RuntimeError, ValueError) as e:
                    print(f"请求失败：{e}")
                    continue
                if result.timeout:
                    print("超时无响应")
                else:
                    print(f"种子 {seed.hex().upper()} 密钥 {key.hex().upper()}")
                    print(f"{result.response.hex().upper()}"
                          + (f"（NRC 0x{result.nrc:02X}）" if result.nrc is not None else "")
                          + f"  {result.elapsed_ms:.0f}ms")
                continue
            try:
                request = parse_hex_bytes(line, "request")
            except ConfigError as e:
                print(f"输入错误：{e}")
                continue
            rec.event(f"手动请求 {request.hex().upper()}")
            try:
                r = uds.request(request)
            except (TimeoutError, IsoTpError, RuntimeError, ValueError) as e:
                # 单条请求失败（如等流控帧超时）不结束会话，可继续输入
                print(f"请求失败：{e}")
                continue
            if r.timeout:
                print("超时无响应")
            else:
                print(f"{r.response.hex().upper()}"
                      + (f"（NRC 0x{r.nrc:02X}）" if r.nrc is not None else "")
                      + f"  {r.elapsed_ms:.0f}ms")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="UDS 诊断测试工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="跑测试用例")
    p_run.add_argument("testcase")
    p_run.add_argument("--config", required=True, help="项目配置 yaml")
    p_run.set_defaults(func=cmd_run)

    p_chat = sub.add_parser("chat", help="交互模式")
    p_chat.add_argument("--config", required=True)
    p_chat.set_defaults(func=cmd_chat)

    p_probe = sub.add_parser("probe", help="单发一条请求")
    p_probe.add_argument("--config", required=True)
    p_probe.add_argument("request", help="十六进制请求，如 22F195")
    p_probe.set_defaults(func=cmd_probe)

    p_unlock = sub.add_parser("unlock", help="0x27 安全解锁")
    p_unlock.add_argument("--config", required=True)
    p_unlock.add_argument("level", help="安全等级（来自配置 security 节，"
                                        "develop 内置）")
    p_unlock.set_defaults(func=cmd_unlock)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2
    except (TimeoutError, RuntimeError, IsoTpError) as e:
        # 运行期异常（驱动/传输失败等），与步骤 FAIL 的退出码 1 区分
        print(f"错误：{e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已中断。已完成的记录见 logs/ 下对应运行目录的 run.log")
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

注意：`cmd_run` 里引用了 `run_flow`，文件顶部 import 行补上：

```python
from flow import ConfigError, load_config, load_testcase, parse_hex_bytes, run_flow
```

- [ ] **Step 14: 运行全量测试确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS（含 test_isotp_*、test_recorder.py 等未改动的测试）

- [ ] **Step 15: 提交**

```bash
git add security.py flow.py diag.py tests/test_security.py tests/test_flow_config.py tests/test_run_flow.py tests/test_diag_cli.py
git commit -m "feat(diag): 配置与测试用例分离，27 解锁改为配置驱动（算法注册表+develop 内置）"
```

---

### Task 3: 存量迁移

**Files:**
- Create: `migrate_split.py`（一次性脚本，用完删除）
- Create: `testcase/N60/*.yaml`（7 个）、`testcase/RX/*.yaml`（2 个）、`testcase/T1LFL1/*.yaml`（1 个）、`testcase/example.yaml`、`testcase/example_mock.yaml`
- Create: `configs/N60/n60.yaml`、`configs/RX/rx.yaml`、`configs/T1LFL1/t1lfl1.yaml`
- Overwrite: `configs/example.yaml`、`configs/example_mock.yaml`（改为纯配置）
- Delete: `configs/N60/`、`configs/RX/`、`configs/T1LFL1/` 下的旧流程 yaml

- [ ] **Step 1: 写迁移脚本 migrate_split.py**

文本手术而非 yaml 往返，保留 steps 里的注释（工单号等）；从 `can:` 行删到 `timing:`/`steps:` 行之前（timing 保留为 testcase 覆盖节）：

```python
"""一次性迁移：configs/<项目>/<流程>.yaml → testcase/<项目>/<流程>.yaml。

文本手术而非 yaml 往返：保留 steps 里的注释（工单号等）与文件头注释。
删除 can/diagnostic 两节；timing 保留为 testcase 的 timing 覆盖节
（存量流程 p2star_ms 有 5000/8000/15000 三档，各自保留原值，行为不变）。
用完即删。
"""
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
OLD = TOOLS / "configs"
NEW = TOOLS / "testcase"

for old in sorted(list(OLD.glob("*/*.yaml")) + list(OLD.glob("example*.yaml"))):
    proj = old.parent.name if old.parent != OLD else ""
    lines = old.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("can:"))
    end = next(i for i, l in enumerate(lines)
               if l.startswith("timing:") or l.startswith("steps:"))
    dest = NEW / proj / old.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")
    print(f"{old.relative_to(TOOLS)} -> {dest.relative_to(TOOLS)}")
```

- [ ] **Step 2: 执行脚本**

Run: `python migrate_split.py`
Expected: 输出 12 行迁移映射（N60×7、RX×2、T1LFL1×1、example×2）

- [ ] **Step 3: 抽查迁移结果**

Run: `head -15 testcase/T1LFL1/verify_2cf5_clear_2a1e.yaml && head -8 testcase/RX/verify_f4cmc59.yaml`
Expected: T1LFL1 文件头注释保留、从 `timing:` 或 `steps:` 开始；RX 文件从 `timing:` 开始。确认无 can/diagnostic 残留。

- [ ] **Step 4: 手写 5 个项目配置**

`configs/N60/n60.yaml`：

```yaml
can:
  device: USBCANFD-200U
  channel: 0
  fd: true
  baudrate: 500000
  data_baudrate: 2000000

diagnostic:
  request_id: 0x70C
  response_id: 0x78C

timing:
  p2_ms: 50
  p2star_ms: 5000
  step_delay_ms: 100

security:
  app:
    seed_sub: 0x01
    key_sub: 0x02
    algorithm: aes_cmac_df_token
    params:
      key_factor: "5E4741955FEF78D046D754F3CDEF170B"
  boot:
    seed_sub: 0x11
    key_sub: 0x12
    algorithm: aes_cmac_df_token
    params:
      key_factor: "A028E273415E3050B8DEF560364A03BD"
```

`configs/RX/rx.yaml`（mask_table 取自 cpj_cmc C 源码 seedToKeyApp）：

```yaml
can:
  device: USBCANFD-200U
  channel: 0
  fd: true
  baudrate: 500000
  data_baudrate: 2000000

diagnostic:
  request_id: 0x712
  response_id: 0x713
  functional_id: 0x7DF

timing:
  p2_ms: 50
  p2star_ms: 5000
  step_delay_ms: 100

security:
  app:
    seed_sub: 0x01
    key_sub: 0x02
    algorithm: cmc_mask_app
    params:
      mask_table: [0xf7cbc641, 0xc81dc123, 0xd88eb629, 0x942e16ad,
                   0x616277ff, 0x8cd16b85, 0x79b09727, 0x7198b307,
                   0x7dd79183, 0x2f7f1b17, 0x0eb2ff43, 0x6c6b262d,
                   0x4efe3885, 0xd028eabd, 0x0ae30f11, 0x3eea5c0d]
  boot:
    seed_sub: 0x11
    key_sub: 0x12
    algorithm: cmc_mask_boot
    params:
      mask_table: [0xf7cbc641, 0xc81dc123, 0xd88eb629, 0x942e16ad,
                   0x616277ff, 0x8cd16b85, 0x79b09727, 0x7198b307,
                   0x7dd79183, 0x2f7f1b17, 0x0eb2ff43, 0x6c6b262d,
                   0x4efe3885, 0xd028eabd, 0x0ae30f11, 0x3eea5c0d]
```

`configs/T1LFL1/t1lfl1.yaml`（解锁源码暂缺，不配 security；develop 内置即可用——T1LFL1 流程用的正是 develop）：

```yaml
can:
  device: USBCANFD-200U
  channel: 0
  fd: true
  baudrate: 500000
  data_baudrate: 2000000

diagnostic:
  request_id: 0x766
  response_id: 0x776
  functional_id: 0x7DF

timing:
  p2_ms: 50
  p2star_ms: 8000
  step_delay_ms: 100
```

`configs/example.yaml`（覆盖旧文件，纯配置，N60 风格）：

```yaml
can:
  device: USBCANFD-200U
  channel: 0
  fd: true
  baudrate: 500000
  data_baudrate: 2000000

diagnostic:
  request_id: 0x70C
  response_id: 0x78C
  functional_id: 0x7DF

timing:
  p2_ms: 50
  p2star_ms: 5000
  step_delay_ms: 100
```

`configs/example_mock.yaml`（覆盖旧文件，纯配置，mock）：

```yaml
can:
  device: mock
  channel: 0
  fd: false
  baudrate: 500000

diagnostic:
  request_id: 0x70C
  response_id: 0x78C
  functional_id: 0x7DF

timing:
  p2_ms: 50
  p2star_ms: 5000
  step_delay_ms: 0
```

- [ ] **Step 5: 校验全部配置×用例组合能加载**

Run:

```bash
python - <<'EOF'
from pathlib import Path
from flow import load_config, load_testcase
for proj in ("N60", "RX", "T1LFL1"):
    cfg = load_config(Path("configs") / proj / f"{proj.lower()}.yaml")
    for tcp in sorted((Path("testcase") / proj).glob("*.yaml")):
        load_testcase(tcp, cfg)
        print(f"OK {tcp}")
for name in ("example", "example_mock"):
    cfg = load_config(Path("configs") / f"{name}.yaml")
    load_testcase(Path("testcase") / f"{name}.yaml", cfg)
    print(f"OK testcase/{name}.yaml")
print("全部加载通过")
EOF
```

Expected: 每个文件一行 OK + 「全部加载通过」。若有 security_access 步骤引用了配置里没有的等级（如 N60 旧流程引用 app/boot——已配，应无问题），按报错补配置。

- [ ] **Step 6: 删除旧流程文件与迁移脚本**

```bash
git rm configs/N60/did_9d01_read_write.yaml configs/N60/probe_1363_nvm.yaml \
       configs/N60/probe_9d01_longtimeout.yaml configs/N60/rid_2013_only.yaml \
       configs/N60/verify_1353_1354.yaml configs/N60/verify_1363_final.yaml \
       configs/N60/verify_1363_nvm.yaml configs/RX/probe_2a0c.yaml \
       configs/RX/verify_f4cmc59.yaml \
       configs/T1LFL1/verify_2cf5_clear_2a1e.yaml
rm migrate_split.py
```

（`git rm` 列表以 Step 2 输出的 10 个项目内文件为准；example.yaml / example_mock.yaml 已被 Step 4 覆盖为纯配置，不需删除。）

- [ ] **Step 7: 全量测试 + mock 冒烟**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS

Run: `python diag.py run testcase/example_mock.yaml --config configs/example_mock.yaml`
Expected: 正常执行（mock 不回帧 → 该用例按 on_fail 跑完，输出 PASS/FAIL/TIMEOUT 汇总；无崩溃、无配置错误即通过）

- [ ] **Step 8: 提交**

```bash
git add configs/ testcase/
git commit -m "feat(diag): 存量配置迁移为 configs/testcase 分离布局"
```

---

### Task 4: README 更新与最终验证

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: 重写 README.md**

完整新内容：

```markdown
# 诊断测试工具（diag）

周立功 USBCANFD-200U 上的 UDS 诊断测试工具。经典 CAN / CAN FD 均支持，
按 YAML 测试用例收发诊断数据，文本/CSV/BLF 三路落盘，支持 0x27 安全解锁。

## 快速上手

    python -m pip install -r requirements.txt
    python diag.py run testcase/example.yaml --config configs/example.yaml

前提：ZLGCAN SDK 已按 `vendor/zlgcan/README.md` 组装、CAN 卡已插、
目标 ECU 已上电。免硬件体验可先跑
`python diag.py run testcase/example_mock.yaml --config configs/example_mock.yaml`。

## 目录结构

    configs/                    # 项目配置（每个项目一份）
      N60/n60.yaml             # can / diagnostic / timing / security
      RX/rx.yaml
      T1LFL1/t1lfl1.yaml
      example.yaml / example_mock.yaml
    testcase/                  # 测试用例（每个流程一份）
      N60/verify_1363_final.yaml   # steps / on_fail / 可选 timing 覆盖
      RX/verify_f4cmc59.yaml
      ...
    logs/                      # 每次运行的 run.log / result.csv / trace.blf

同一测试用例换 `--config` 即可跑不同项目。

## 命令一览

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `run` | 跑测试用例 | `python diag.py run testcase/N60/x.yaml --config configs/N60/n60.yaml` |
| `chat` | 交互模式，逐条发请求 | `python diag.py chat --config configs/N60/n60.yaml` |
| `probe` | 单发一条请求 | `python diag.py probe --config configs/N60/n60.yaml 22F195` |
| `unlock` | 0x27 安全解锁 | `python diag.py unlock --config configs/N60/n60.yaml app` |

## 配置文件（configs/）

每个项目一份，共四节：

- **can** — 总线接入：`device`（周立功设备名，`mock` 走内置 Mock 驱动、
  免硬件）、`channel` 通道号、`fd` 是否 CAN FD、`baudrate` 仲裁域波特率；
  `data_baudrate` 为 FD 数据域波特率（可省略，默认 2000000）。
- **diagnostic** — 诊断寻址：`request_id` 请求 ID、`response_id` 响应 ID、
  `functional_id` 功能寻址 ID（可选，功能寻址步骤必需）。三个 ID 均限
  11 位标准帧范围 0x001-0x7FF。
- **timing** — 超时与节奏：`p2_ms` 单响应超时（默认 50）、`p2star_ms`
  收到 NRC 0x78 后的等待窗口（默认 5000）、`step_delay_ms` 步骤间默认
  间隔（默认 100）。三项均可省略。
- **security** — 0x27 安全解锁等级（见下）。可选。

## 测试用例（testcase/）

只含流程，共三节：

- **steps** — 步骤列表：每步必填 `name`，普通步骤另填 `request`（引号
  包裹的十六进制字符串，如 `"22F195"`）；可选 `expect`（期望响应前缀，
  省略则只发不判、超时不算失败）、`timeout_ms`（覆盖该步的 P2 超时）、
  `delay_after_ms`（该步收尾延时，整体覆盖 `step_delay_ms` 而非叠加）、
  `addressing`（`physical` 默认 / `functional`：发完即记、不支持
  `expect`，请求长度不得超过单帧上限——经典 7 字节、FD 62 字节）。
  另一类是安全解锁步骤：填 `security_access: <等级名>`（不可与
  `request`/`expect`/功能寻址同用，等级须在配置 security 节或为内置
  develop），执行完整 27 取种子 → 算密钥 → 发密钥序列，密钥响应
  `67+key_sub` 判 PASS。
- **on_fail** — 失败策略：`abort`（默认，首个 FAIL/TIMEOUT 即中止）或
  `continue`（跑完全部步骤）。
- **timing**（可选）— 覆盖配置的 timing 默认值，只覆盖出现的键。

## security 节（0x27 解锁配置化）

每个项目的解锁不同：等级、子功能号、算法与密钥材料都写在配置里。

```yaml
security:
  app:
    seed_sub: 0x01          # 取种子子功能号
    key_sub: 0x02           # 发密钥子功能号，必须 = seed_sub + 1
    algorithm: cmc_mask_app # 算法名（见下表）
    params:                 # 算法参数，随算法而定
      mask_table: [0xf7cbc641, ...]  # 16 个 u32
```

可用算法（种子/密钥长度由算法决定，无需配置）：

| 算法名 | 用途 | params | 密钥计算 |
| --- | --- | --- | --- |
| `aes_cmac_df_token` | N60 app/boot | `key_factor`（16B hex） | SK=CMAC(KF,DF)，Key=DF‖CMAC(SK,Seed) |
| `aes_cmac_seed` | develop 等 | `key`（16B hex）、`magic`（16B hex） | Key=CMAC(key,Seed) |
| `cmc_mask_app` | RX app | `mask_table`（16×u32） | cpj_cmc seedToKeyApp |
| `cmc_mask_boot` | RX boot | `mask_table` | cpj_cmc seedToKeyBoot |

**develop（61/62，公司内部通用）内置**：所有项目自动可用，无需配置；
配置里写 `security.develop` 可覆盖。新客户算法 = 在 security.py 注册表
新增一个函数，参数仍走配置。

## run — 跑测试用例

    python diag.py run testcase/N60/x.yaml --config configs/N60/n60.yaml

输出汇总与退出码：

    PASS 3 / FAIL 0 / TIMEOUT 0 / 共 3/3     # 退出码 0
    PASS 1 / FAIL 1 / TIMEOUT 0 / 共 2/3     # 有 FAIL/TIMEOUT，退出码 1

结果同时落盘到 `logs/<用例名>_<时间戳>/`（见「输出文件」）。

## chat — 交互模式

    python diag.py chat --config configs/N60/n60.yaml

逐条输入十六进制请求，即时显示响应与耗时：

    diag> 22F195
    62F19520533031302E303039  16ms
    diag> unlock app
    种子 0147C899BC7BB019... 密钥 5D16181468FEDF47...
    6702  15ms
    diag> :q

- 负响应显示 NRC：`7F2231（NRC 0x31）`；无响应显示 `超时无响应`。
- `unlock <等级>` 执行安全解锁（等级来自配置 + 内置 develop），
  失败不断线可继续输入。
- `:q` / `:quit` / `exit` 退出；单条请求失败不结束会话。

## probe — 单发一条

    python diag.py probe --config configs/N60/n60.yaml 22F195
    响应：62F19520533031302E303039

## unlock — 安全解锁

    python diag.py unlock --config configs/N60/n60.yaml app

等级来自配置 security 节 + 内置 develop（61/62）。

**会话前置**：默认会话下 ECU 拒绝 0x27（NRC 0x7F），须先进扩展会话；
`boot` 级在应用（扩展）会话同样被拒（NRC 0x7E），需 boot/编程上下文。
单条命令不自动切会话——用 chat 先发 `1003` 再 `unlock`，或流程里在
解锁步骤前加进扩展会话步骤。

退出码与 `probe` 一致：0 成功、1 超时无响应、2 负响应或未知等级
（含 NRC 0x35 密钥错误）。

## 输出文件

每次运行生成 `logs/<用例名>_<时间戳>/`：

- `run.log`  人读文本流水（步骤、判定、NRC、0x78 事件、解锁种子/密钥）
- `result.csv`  UDS 级结果表（seq/步骤/请求/响应/NRC/判定/耗时/时间戳）
- `trace.blf`  总线原始帧，CANoe 可直接回放

## 退出码约定

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | run：有 FAIL/TIMEOUT；probe/unlock：ECU 超时无响应 |
| 2 | 配置错误（含未知安全等级）；probe/unlock：收到负响应 |
| 3 | 运行期异常（驱动/传输失败等） |
| 130 | Ctrl+C 中断（已落盘记录不丢） |

## 常见问题

- **`打开 USBCANFD-200U#0 失败`** — 检查 USB 连接与设备索引；确认没有
  其他程序（zcanpro 等）占用设备。
- **`CAN FD 发送失败`** — 多为物理层问题：ECU 掉电、CAN 线未接。ECU
  上电有启动时间，重试前等几秒。
- **`错误：配置错误 ...`** — YAML 有未知字段或缺字段；错误信息会指明
  位置（如 `steps[2].request: 缺失`、`security.app.algorithm: ...`）。
- **取种子回 NRC 0x7F** — 默认会话不支持 0x27，先进扩展会话（1003）。
- **boot 级回 NRC 0x7E** — 应用会话不支持 boot 解锁，属预期行为。
- **SF 帧要求**：该 ECU 只接受恰好 8 字节的 SF 帧，工具已自动补齐，
  无需关心；写其他测试桩模拟此 ECU 时需注意。

## 测试

    python -m pytest tests/ -v
```

- [ ] **Step 2: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS

Run: `python diag.py run testcase/example_mock.yaml --config configs/example_mock.yaml`
Expected: 正常执行无配置错误

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs(diag): README 同步 configs/testcase 分离与 security 配置"
```

- [ ] **Step 4: 真机验证（用户执行，工具侧无法替代）**

1. RX 回归：`python diag.py chat --config configs/RX/rx.yaml`，先 `1003`
   进扩展会话，再 `unlock app` —— 验证 2 字节种子 → 4 字节密钥的
   mask_table 算法与 01/02 子功能号（子功能号暂按惯例配置，若 ECU 回
   NRC 0x12 需对照 RX Dcm 配置修正 rx.yaml 的 seed_sub/key_sub）。
2. N60 回归：`python diag.py unlock --config configs/N60/n60.yaml app`，
   确认 6702。
3. T1LFL1 回归：跑 `testcase/T1LFL1/verify_2cf5_clear_2a1e.yaml`
   （develop 解锁内置，应与迁移前行为一致）。

---

## Self-Review 结果

- **Spec 覆盖**：目录拆分（Task 2/3）、CLI 两文件（Task 2 Step 13）、
  security 节与算法注册表（Task 1/2）、develop 内置（Task 2）、
  存量迁移含 example 与 T1LFL1 不配 security（Task 3）、README（Task 4）、
  真机验证（Task 4 Step 4）——spec 各节均有对应任务。
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`perform_unlock(uds, level: SecLevel)`、
  `load_testcase(path, cfg)`、`run_flow(cfg, testcase, driver, run_dir)`、
  `levels_from_config(raw) -> dict[str, SecLevel]` 在各任务/测试中一致；
  `migrate_split.py` 文本手术已验证 T1LFL1 头部注释与无 timing 文件
  （`steps:` 起点）两种边界。
