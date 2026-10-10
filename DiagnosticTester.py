"""诊断测试工具 CLI。

用法：
  python DiagnosticTester.py run <testcase.yaml> --config <config.yaml>   跑测试用例
  python DiagnosticTester.py chat --config <config.yaml>                  交互模式
  python DiagnosticTester.py probe --config <config.yaml> 22F195          单发一条请求
  python DiagnosticTester.py unlock --config <config.yaml> app             0x27 安全解锁
  python DiagnosticTester.py dem --config <config.yaml>                    故障检测报告（DTC/event/fimfid）
  python DiagnosticTester.py dem-map --config <config.yaml>                手动粘贴响应离线解析 DEM map
"""
from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dem import load_maps, run_dem, run_dem_manual
from flow import ConfigError, load_config, load_testcase, parse_hex_bytes, run_flow
from isotp import IsoTpError, IsoTpTransport
from recorder import Recorder
from security import UnlockError, perform_unlock
from uds import UdsClient

TOOLS_DIR = Path(__file__).resolve().parent
LOGS_DIR = TOOLS_DIR / "logs"


def make_driver(cfg):
    can_cfg = cfg["can"]
    device = can_cfg["device"]
    if device == "mock":
        tests_dir = str(TOOLS_DIR / "tests")
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from mock_driver import MockCanDriver
        return MockCanDriver()
    from can_driver import CxCanFdDriver, ZlgCanDriver
    common = dict(channel=can_cfg["channel"], baudrate=can_cfg["baudrate"],
                  data_baudrate=can_cfg.get("data_baudrate", 2000000),
                  fd=can_cfg["fd"])
    if device in ZlgCanDriver._DEV_TYPE_CONSTS:
        return ZlgCanDriver(device=device, **common)
    if device in CxCanFdDriver._DEV_TYPE_CONSTS:
        return CxCanFdDriver(device=device, **common)
    raise RuntimeError(
        f"不支持的设备 {device}，可选：mock、"
        f"{'、'.join((*ZlgCanDriver._DEV_TYPE_CONSTS, *CxCanFdDriver._DEV_TYPE_CONSTS))}")


def new_run_dir(base: Path, prefix: str | None = None) -> Path:
    """按时间戳建运行目录；同秒重复运行追加 _1/_2… 后缀，避免互相覆盖。

    prefix 为 None：子目录直接用时间戳（run 命令，建在用例 yaml 同目录下）；
    给定 prefix：扁平命名 <prefix>_<时间戳>（probe/chat/unlock/dem，建在 logs/ 下）。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i in range(100):
        name = ts if i == 0 else f"{ts}_{i}"
        d = base / (f"{prefix}_{name}" if prefix else name)
        try:
            d.mkdir(parents=True, exist_ok=False)
            return d
        except FileExistsError:
            continue
    raise RuntimeError(f"运行目录碰撞超过 100 个候选：{base}")


def config_project(config_path) -> str:
    """日志前缀用的项目名：configs/<项目>/x.yaml 取子目录名，
    configs 根下的配置（example 等）取配置文件名。"""
    p = Path(config_path)
    parent = p.parent.name
    if parent in ("", ".", "configs"):
        return p.stem
    return parent


def cmd_run(args):
    cfg = load_config(args.config)
    tc = load_testcase(args.testcase, cfg)
    driver = make_driver(cfg)
    # 运行日志放在用例 yaml 同目录下的时间戳子目录：
    # testcase/<项目>/<用例>/<用例>.yaml → testcase/<项目>/<用例>/<时间戳>/
    run_dir = new_run_dir(Path(args.testcase).resolve().parent)
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
def open_session(cfg, project: str, prefix: str):
    """probe/chat/unlock/dem 共用的会话资源：driver → 记录器 → 传输 → UDS 客户端，退出时逆序收尾。

    日志目录 = logs/<项目名>_<命令名>_<时间戳>（扁平）。run 的日志在用例 yaml
    同目录下的时间戳子目录（与步骤执行耦合），cmd_run 不走此入口。
    """
    driver = make_driver(cfg)
    rec = Recorder(new_run_dir(LOGS_DIR, prefix=f"{project}_{prefix}"))
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
    with open_session(cfg, config_project(args.config), "probe") as (_driver, _rec, _transport, uds):
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
    with open_session(cfg, config_project(args.config), "unlock") as (_driver, rec, _transport, uds):
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


def cmd_dem(args):
    cfg = load_config(args.config)
    maps = load_maps(Path(args.config).parent)
    with open_session(cfg, config_project(args.config), "dem") as (_driver, rec, _transport, uds):
        def emit(msg):
            print(msg)
            if msg:
                rec.event(msg)
            else:
                rec.blank()  # 节间空行：落盘不带时间戳前缀
        return run_dem(uds, maps, emit)


def cmd_dem_map(args):
    # 离线手动解析：不触硬件，--config 只用于定位并校验 map 目录
    load_config(args.config)
    maps = load_maps(Path(args.config).parent)
    return run_dem_manual(maps, print)


def cmd_chat(args):
    cfg = load_config(args.config)
    levels = cfg["security_levels"]
    with open_session(cfg, config_project(args.config), "chat") as (_driver, rec, _transport, uds):
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

    p_dem = sub.add_parser("dem", help="拉取 DTC/event/fimfid 故障报告")
    p_dem.add_argument("--config", required=True)
    p_dem.set_defaults(func=cmd_dem)

    p_demmap = sub.add_parser("dem-map", help="手动粘贴响应离线解析 DEM map（不连 ECU）")
    p_demmap.add_argument("--config", required=True)
    p_demmap.set_defaults(func=cmd_dem_map)

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
