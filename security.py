"""UDS 0x27 安全解锁：种子/密钥计算与完整解锁序列。

算法对齐 mcu_app_j6/cpj_baic/dia_cfg b81vs 变体（与 n60 实现一致）：
- app/boot（DF+Token 认证）：SK = AES-CMAC(KeyFactor, DF)、Token = AES-CMAC(SK, Seed)，
  发送 Key = DF(16B) || Token(16B)；DF 由客户端任选。
- develop：取种子请求携带 16 字节魔数，Key = AES-CMAC(开发密钥, Seed)（16B）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import algorithms

from uds import UdsClient

# BAIC 规范 key factor（b81vs/n60/b70ks 通用）
KF_UDS = bytes.fromhex("5E4741955FEF78D046D754F3CDEF170B")
KF_BOOT = bytes.fromhex("A028E273415E3050B8DEF560364A03BD")
DEV_KEY = bytes.fromhex("FF0200020503191039490D98AD52DEFF")
# develop 级取种子请求需携带的魔数（客户端权限校验）
DEV_MAGIC = bytes.fromhex("CFBB609DBC8293CCA532D21409774D84")

SEED_LEN = 16


@dataclass(frozen=True)
class SecLevel:
    name: str
    seed_sub: int
    key_sub: int
    key_len: int
    key_factor: bytes | None  # None 表示 develop 单层 CMAC


LEVELS = {
    "app": SecLevel("app", 0x01, 0x02, 32, KF_UDS),
    "boot": SecLevel("boot", 0x11, 0x12, 32, KF_BOOT),
    "develop": SecLevel("develop", 0x61, 0x62, 16, None),
}


def _cmac(key: bytes, data: bytes) -> bytes:
    c = cmac.CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def _level(level: str) -> SecLevel:
    if level not in LEVELS:
        raise ValueError(f"未知安全等级 {level}，可选 {sorted(LEVELS)}")
    return LEVELS[level]


def seed_request(level: str) -> bytes:
    """取种子请求：develop 级携带魔数。"""
    lvl = _level(level)
    req = bytes([0x27, lvl.seed_sub])
    if lvl.key_factor is None:
        req += DEV_MAGIC
    return req


def compute_key(level: str, seed: bytes, df: bytes | None = None) -> bytes:
    """由种子计算密钥。app/boot 的 DF 缺省随机生成并内嵌在返回值前 16 字节。"""
    lvl = _level(level)
    if len(seed) != SEED_LEN:
        raise ValueError(f"种子长度应为 {SEED_LEN}，当前 {len(seed)}")
    if lvl.key_factor is None:
        return _cmac(DEV_KEY, seed)
    df = os.urandom(16) if df is None else df
    sk = _cmac(lvl.key_factor, df)
    return df + _cmac(sk, seed)


class UnlockError(Exception):
    """解锁序列在种子阶段失败（超时/负响应/响应格式异常）。timeout 标记失败原因。"""

    def __init__(self, msg, timeout: bool = False):
        super().__init__(msg)
        self.timeout = timeout


def perform_unlock(uds: UdsClient, level: str, timeout_ms: int | None = None,
                   log=None):
    """执行完整 0x27 序列：取种子 → 计算密钥 → 发密钥。

    返回 (seed, key, result)；result 为密钥请求的 UdsResult（response
    以 67+key_sub 开头即成功）。种子阶段失败抛 UnlockError。
    """
    log = log or (lambda *a, **k: None)
    lvl = _level(level)

    r = uds.request(seed_request(level), timeout_ms=timeout_ms)
    if r.timeout:
        raise UnlockError(f"取种子超时（27 {lvl.seed_sub:02X}）", timeout=True)
    if r.nrc is not None:
        raise UnlockError(f"取种子负响应 NRC 0x{r.nrc:02X}")
    expect = bytes([0x67, lvl.seed_sub])
    if r.response is None or not r.response.startswith(expect) \
            or len(r.response) < len(expect) + SEED_LEN:
        raise UnlockError(f"取种子响应异常："
                          f"{r.response.hex().upper() if r.response else '无'}")
    seed = r.response[len(expect):len(expect) + SEED_LEN]
    log(f"安全解锁[{level}] 种子 {seed.hex().upper()}")

    key = compute_key(level, seed)
    log(f"安全解锁[{level}] 密钥 {key.hex().upper()}")
    result = uds.request(bytes([0x27, lvl.key_sub]) + key, timeout_ms=timeout_ms)
    return seed, key, result


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
