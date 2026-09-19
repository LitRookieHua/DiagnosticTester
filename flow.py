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


def _rx_text(r: UdsResult) -> str:
    return r.response.hex().upper() if r.response else "(timeout)"


def _result_text(verdict: str, expect: bytes | None, r: UdsResult) -> str:
    parts = [f"Result: {verdict}"]
    if expect is not None:
        parts.append(f"Expected: {expect.hex().upper()}")
    if r.response is not None:
        parts.append(f"Actual: {r.response.hex().upper()}")
    if r.nrc is not None:
        parts.append(f"NRC: 0x{r.nrc:02X}")
    return " ".join(parts)


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
            rec.event(f"Flow start: {len(steps)} steps, on_fail={on_fail}")
            for i, step in enumerate(steps, 1):
                counters["total"] += 1
                rec.blank()
                if step.security_access is not None:
                    lvl = levels[step.security_access]
                    log(f"[{i}] {step.name} (security access: {step.security_access})")
                    try:
                        seed, key, result = perform_unlock(
                            uds, lvl, timeout_ms=step.timeout_ms, log=log)
                    except UnlockError as e:
                        result, verdict = UdsResult(), "FAIL"
                        log(f"[{i}] Result: FAIL ({e})")
                    else:
                        ok = (result.response is not None
                              and result.response.startswith(
                                  bytes([0x67, lvl.key_sub])))
                        verdict = ("PASS" if ok
                                   else "TIMEOUT" if result.timeout else "FAIL")
                        log(f"[{i}] UDS RX: {_rx_text(result)}")
                        log(f"[{i}] {_result_text(verdict, None, result)}")
                    rec.step(i, step.name, "physical",
                             f"27{lvl.seed_sub:02X}+27{lvl.key_sub:02X}", result, verdict)
                elif step.addressing == "functional":
                    log(f"[{i}] {step.name} (functional addressing, no response expected)")
                    log(f"[{i}] UDS TX: {step.request.hex().upper()}")
                    frames = encode(step.request, cfg["can"]["fd"])
                    for f in frames:
                        frame = CanFrame(diag["functional_id"], f,
                                         is_fd=cfg["can"]["fd"],
                                         bitrate_switch=cfg["can"]["fd"])
                        driver.send(frame)
                        rec.frame(frame, False)
                    # 发完即记一行 SENT：CSV 行数与已执行步骤对齐、与 abort 跳过可区分
                    rec.step(i, step.name, step.addressing,
                             step.request.hex().upper(), UdsResult(), "SENT")
                    log(f"[{i}] Result: SENT")
                    counters["pass"] += 1
                    time.sleep(delay_s(step))
                    continue

                else:
                    log(f"[{i}] {step.name}")
                    log(f"[{i}] UDS TX: {step.request.hex().upper()}")
                    result = uds.request(step.request, timeout_ms=step.timeout_ms)
                    verdict = judge(step, result)
                    rec.step(i, step.name, step.addressing,
                             step.request.hex().upper(), result, verdict)
                    log(f"[{i}] UDS RX: {_rx_text(result)}")
                    log(f"[{i}] {_result_text(verdict, step.expect, result)}")
                if verdict == "PASS":
                    counters["pass"] += 1
                elif verdict == "FAIL":
                    counters["fail"] += 1
                else:
                    counters["timeout"] += 1
                if verdict != "PASS" and on_fail == "abort":
                    log("Step failed and on_fail=abort, aborting flow")
                    break
                time.sleep(delay_s(step))
        finally:
            transport.stop()
            rec.event(f"Flow finished: PASS {counters['pass']} / FAIL {counters['fail']}"
                      f" / TIMEOUT {counters['timeout']}"
                      f" / total {counters['total']}/{len(steps)}")
    return counters
