"""流程配置：加载、校验、步骤执行。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from can_driver import CanFrame
from isotp import IsoTpTransport, encode
from uds import UdsClient, UdsResult

HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
STEP_FIELDS = {"name", "request", "expect", "timeout_ms", "delay_after_ms", "addressing"}


class ConfigError(Exception):
    pass


@dataclass
class Step:
    name: str
    request: bytes
    expect: bytes | None = None
    timeout_ms: int | None = None
    delay_after_ms: int | None = None
    addressing: str = "physical"


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
        return v
    s = str(v).strip().lower().replace("0x", "")
    try:
        return int(s, 16)
    except ValueError:
        raise ConfigError(f"{where}: '{v}' 不是合法 CAN ID")


def _check_num(d, k, path, min_=0):
    v = d.get(k)
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{path}.{k}: 应为数字，当前是 {type(v).__name__}: {v!r}")
    if v < min_:
        raise ConfigError(f"{path}.{k}: 不能小于 {min_}，当前 {v}")


def load_config(path, need_steps: bool = True) -> dict:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 语法错误：{e}")
    if not isinstance(raw, dict):
        raise ConfigError("配置根节点必须是映射")
    unknown = set(raw) - {"can", "diagnostic", "timing", "steps", "on_fail"}
    if unknown:
        raise ConfigError(f"未知顶层字段 {sorted(unknown)}")

    can = raw.get("can")
    if not isinstance(can, dict):
        raise ConfigError("can: 缺失或不是映射")
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
    for k in ("request_id", "response_id"):
        if k not in diag:
            raise ConfigError(f"diagnostic.{k}: 缺失")
        diag[k] = parse_id(diag[k], f"diagnostic.{k}")
    if diag.get("functional_id") is not None:
        diag["functional_id"] = parse_id(diag["functional_id"], "diagnostic.functional_id")

    timing = raw.get("timing")
    if timing is None:
        timing = {}
    if not isinstance(timing, dict):
        raise ConfigError("timing: 必须是映射")
    for k in ("p2_ms", "p2star_ms", "step_delay_ms"):
        _check_num(timing, k, "timing")
    timing.setdefault("p2_ms", 50)
    timing.setdefault("p2star_ms", 5000)
    timing.setdefault("step_delay_ms", 100)

    on_fail = raw.get("on_fail", "abort")
    if on_fail not in ("abort", "continue"):
        raise ConfigError("on_fail: 只能是 abort 或 continue")

    steps = []
    if need_steps:
        raw_steps = raw.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ConfigError("steps: 必须是非空列表")
        for i, s in enumerate(raw_steps):
            where = f"steps[{i}]"
            if not isinstance(s, dict):
                raise ConfigError(f"{where}: 必须是映射")
            unknown = set(s) - STEP_FIELDS
            if unknown:
                raise ConfigError(f"{where}: 未知字段 {sorted(unknown)}")
            for k in ("name", "request"):
                if k not in s:
                    raise ConfigError(f"{where}.{k}: 缺失")
            for k in ("timeout_ms", "delay_after_ms"):
                _check_num(s, k, where)
            request = parse_hex_bytes(s["request"], f"{where}.request")
            expect = (parse_hex_bytes(s["expect"], f"{where}.expect")
                      if s.get("expect") is not None else None)
            addressing = s.get("addressing", "physical")
            if addressing not in ("physical", "functional"):
                raise ConfigError(f"{where}.addressing: 只能是 physical 或 functional")
            if addressing == "functional":
                if diag.get("functional_id") is None:
                    raise ConfigError(f"{where}: 功能寻址需要 diagnostic.functional_id")
                sf_max = 62 if can["fd"] else 7
                if len(request) > sf_max:
                    raise ConfigError(f"{where}: 功能寻址请求超过单帧上限 {sf_max} 字节")
            steps.append(Step(name=str(s["name"]), request=request, expect=expect,
                              timeout_ms=s.get("timeout_ms"),
                              delay_after_ms=s.get("delay_after_ms"),
                              addressing=addressing))

    return {"can": can, "diagnostic": diag, "timing": timing,
            "steps": steps, "on_fail": on_fail}


def judge(step: Step, r: UdsResult) -> str:
    if step.expect is None:
        return "PASS"  # 无期望即记录：只发不判，超时不算失败
    if r.timeout:
        return "TIMEOUT"
    if r.response is not None and r.response.startswith(step.expect):
        return "PASS"
    return "FAIL"
