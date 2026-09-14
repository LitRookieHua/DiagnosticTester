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


def run_flow(cfg: dict, driver, run_dir):
    """执行流程，返回汇总 dict（total/pass/fail/timeout）。driver 为已就绪的 CanDriver。"""
    from recorder import Recorder

    diag, timing = cfg["diagnostic"], cfg["timing"]
    rec = Recorder(run_dir)

    def tap(frame, is_rx):
        rec.frame(frame, is_rx)

    def log(msg):
        rec.event(msg)

    transport = IsoTpTransport(driver, tx_id=diag["request_id"], rx_id=diag["response_id"],
                               is_fd=cfg["can"]["fd"], tap=tap, log=log)
    transport.start()
    uds = UdsClient(transport, p2_ms=timing["p2_ms"], p2star_ms=timing["p2star_ms"], log=log)
    rec.event(f"流程开始：共 {len(cfg['steps'])} 步，on_fail={cfg['on_fail']}")

    counters = {"total": 0, "pass": 0, "fail": 0, "timeout": 0}
    try:
        for i, step in enumerate(cfg["steps"], 1):
            counters["total"] += 1
            if step.addressing == "functional":
                frames = encode(step.request, cfg["can"]["fd"])
                for f in frames:
                    frame = CanFrame(diag["functional_id"], f,
                                     is_fd=cfg["can"]["fd"], bitrate_switch=cfg["can"]["fd"])
                    driver.send(frame)
                    rec.frame(frame, False)
                rec.event(f"[{i}] {step.name} 功能寻址发送 {step.request.hex().upper()}（不等待响应）")
                counters["pass"] += 1
                time.sleep((step.delay_after_ms if step.delay_after_ms is not None
                            else timing["step_delay_ms"]) / 1000.0)
                continue

            log(f"[{i}] {step.name} 发送 {step.request.hex().upper()}"
                f"（期望 {step.expect.hex().upper() if step.expect else '无'}）")
            result = uds.request(step.request, timeout_ms=step.timeout_ms)
            verdict = judge(step, result)
            rec.step(i, step.name, step.addressing, step.request.hex().upper(), result, verdict)
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
            if verdict != "PASS" and cfg["on_fail"] == "abort":
                log("步骤失败且 on_fail=abort，中止流程")
                break
            time.sleep((step.delay_after_ms if step.delay_after_ms is not None
                        else timing["step_delay_ms"]) / 1000.0)
    finally:
        transport.stop()
        rec.event(f"流程结束：PASS {counters['pass']} / FAIL {counters['fail']}"
                  f" / TIMEOUT {counters['timeout']} / 共 {counters['total']}")
        rec.close()
    return counters
