"""UDS 0x27 安全解锁：算法注册表、等级构建与完整解锁序列。

算法按项目不同在配置 security 节中选择（algorithm 字段），密钥材料
（key factor / mask table / 魔数）全部来自配置参数；develop（61/62，
公司内部通用）为内置默认，配置可覆盖。

算法对照源码：
- aes_cmac_df_token / aes_cmac_seed — cpj_baic n60 变体
- cmc_mask_app / cmc_mask_boot — cpj_cmc（RX）
- t1lfl1_crc16 — cpj_chery veh_t1lfl1（T1LFL1 app，CRC 式位运算）
- gac_shift_xor — cpj_gac（T75 app/boot，移位乘法异或，常量分等级）
"""
from __future__ import annotations

import os
from dataclasses import dataclass

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
        return ALGORITHMS[self.algorithm]["key"](seed, self.params, df)


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
    """develop/V2XEU app：Key=CMAC(key,Seed)。"""
    return _cmac(params["key"], seed)


def _key_t1lfl1_crc16(seed: bytes, params: dict, df: bytes | None) -> bytes:
    """T1LFL1 app：CRC 式位运算（uint16 回绕），密钥 2 字节大端。

    对照 ov_dia_0x27_security_access.cpp Dcm_CompareKey：初值 0xFFFE，
    每字节异或到高 8 位后按位移 8 次，最高位为 1 时按 0x0080 位选
    多项式 0x8408 / 0x8025 异或。
    """
    k = 0xFFFE
    for b in seed:
        k ^= b << 8
        for _ in range(8):
            if k & 0x8000:
                poly = 0x8408 if k & 0x0080 else 0x8025
                k = ((k << 1) ^ poly) & 0xFFFF
            else:
                k = (k << 1) & 0xFFFF
    return k.to_bytes(2, "big")


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


def _key_gac_shift_xor(seed: bytes, params: dict, df: bytes | None) -> bytes:
    """GAC T75 app/boot：uint32 移位乘法异或，密钥 4 字节大端。

    对照 cpj_gac ov_dia_0x27_security_access.cpp computeKey：
    k=(seed>>9)|(seed<<22); k*=6; k^=constant; k=(k<<14)|(k>>17)。
    全程 uint32 回绕；app/boot 仅 constant 不同。
    """
    s = int.from_bytes(seed, "big")
    k = ((s >> 9) | (s << 22)) & 0xFFFFFFFF
    k = (k * 6) & 0xFFFFFFFF
    k ^= int.from_bytes(params["constant"], "big")
    k = ((k << 14) | (k >> 17)) & 0xFFFFFFFF
    return k.to_bytes(4, "big")


# 算法注册表：计算函数 + 固有 (seed_len, key_len) + params 键 → hex 字节数
# （mask_table 特殊：16 项 u32 列表；optional_params 可省略）
ALGORITHMS: dict[str, dict] = {
    "aes_cmac_df_token": {"key": _key_aes_cmac_df_token,
                          "seed_len": 16, "key_len": 32,
                          "params": {"key_factor": 16}},
    "aes_cmac_seed": {"key": _key_aes_cmac_seed, "seed_len": 16, "key_len": 16,
                      "params": {"key": 16}, "optional_params": {"magic": 16}},
    "cmc_mask_app": {"key": _key_cmc_mask_app, "seed_len": 2, "key_len": 4,
                     "params": {"mask_table": 16}},
    "cmc_mask_boot": {"key": _key_cmc_mask_boot, "seed_len": 2, "key_len": 4,
                      "params": {"mask_table": 16}},
    "t1lfl1_crc16": {"key": _key_t1lfl1_crc16, "seed_len": 2, "key_len": 2,
                     "params": {}},
    "gac_shift_xor": {"key": _key_gac_shift_xor, "seed_len": 4, "key_len": 4,
                      "params": {"constant": 4}},
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
    optional = spec.get("optional_params", {})
    unknown = sorted(set(raw_params) - set(spec["params"]) - set(optional))
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
    for k, n in optional.items():
        if k in raw_params:
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
        raise UnlockError(f"seed request timeout (27 {level.seed_sub:02X})",
                          timeout=True)
    if r.nrc is not None:
        raise UnlockError(f"seed request NRC 0x{r.nrc:02X}")
    expect = bytes([0x67, level.seed_sub])
    if r.response is None or not r.response.startswith(expect) \
            or len(r.response) < len(expect) + level.seed_len:
        raise UnlockError(f"unexpected seed response: "
                          f"{r.response.hex().upper() if r.response else 'none'}")
    seed = r.response[len(expect):len(expect) + level.seed_len]
    log(f"Security[{level.name}] seed {seed.hex().upper()}")

    key = level.compute_key(seed)
    log(f"Security[{level.name}] key {key.hex().upper()}")
    result = uds.request(bytes([0x27, level.key_sub]) + key,
                         timeout_ms=timeout_ms)
    return seed, key, result
