"""诊断测试工具 CLI。

用法：
  python diag.py run <flow.yaml>            跑配置流程
  python diag.py chat --config <flow.yaml>  交互模式
  python diag.py probe --config <flow.yaml> 单发一条请求（如 22F195）
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from flow import ConfigError, load_config, parse_hex_bytes, run_flow
from isotp import IsoTpTransport
from recorder import Recorder
from uds import UdsClient

TOOLS_DIR = Path(__file__).resolve().parent
LOGS_DIR = TOOLS_DIR / "logs"


def make_driver(cfg):
    can_cfg = cfg["can"]
    if can_cfg["device"] == "mock":
        sys.path.insert(0, str(TOOLS_DIR / "tests"))
        from mock_driver import MockCanDriver
        return MockCanDriver()
    from can_driver import ZlgCanDriver
    return ZlgCanDriver(device=can_cfg["device"], channel=can_cfg["channel"],
                        baudrate=can_cfg["baudrate"],
                        data_baudrate=can_cfg.get("data_baudrate", 2000000),
                        fd=can_cfg["fd"])


def new_run_dir(prefix: str) -> Path:
    d = LOGS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_run(args):
    cfg = load_config(args.config)
    driver = make_driver(cfg)
    run_dir = new_run_dir(Path(args.config).stem)
    print(f"运行目录：{run_dir}")
    counters = run_flow(cfg, driver, run_dir)
    driver.close()
    print(f"PASS {counters['pass']} / FAIL {counters['fail']} "
          f"/ TIMEOUT {counters['timeout']} / 共 {counters['total']}")
    return 0 if counters["fail"] == 0 and counters["timeout"] == 0 else 1


def build_transport(cfg, driver, rec):
    diag = cfg["diagnostic"]
    t = IsoTpTransport(driver, tx_id=diag["request_id"], rx_id=diag["response_id"],
                       is_fd=cfg["can"]["fd"],
                       tap=lambda f, rx: rec.frame(f, rx), log=rec.event)
    t.start()
    return t


def cmd_probe(args):
    cfg = load_config(args.config, need_steps=False)
    request = parse_hex_bytes(args.request, "request")
    driver = make_driver(cfg)
    rec = Recorder(new_run_dir("probe"))
    transport = build_transport(cfg, driver, rec)
    uds = UdsClient(transport, p2_ms=cfg["timing"]["p2_ms"],
                    p2star_ms=cfg["timing"]["p2star_ms"], log=rec.event)
    try:
        r = uds.request(request)
        print(f"响应：{r.response.hex().upper() if r.response else '超时无响应'}"
              + (f"（NRC 0x{r.nrc:02X}）" if r.nrc is not None else ""))
        return 0
    finally:
        transport.stop()
        rec.close()
        driver.close()


def cmd_chat(args):
    cfg = load_config(args.config, need_steps=False)
    driver = make_driver(cfg)
    rec = Recorder(new_run_dir("chat"))
    print(f"交互模式（日志：{rec.run_dir}）。输入十六进制请求如 22F195，:q 退出")
    transport = build_transport(cfg, driver, rec)
    uds = UdsClient(transport, p2_ms=cfg["timing"]["p2_ms"],
                    p2star_ms=cfg["timing"]["p2star_ms"], log=rec.event)
    try:
        while True:
            try:
                line = input("diag> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in (":q", ":quit", "exit"):
                break
            try:
                request = parse_hex_bytes(line, "request")
            except ConfigError as e:
                print(f"输入错误：{e}")
                continue
            rec.event(f"手动请求 {request.hex().upper()}")
            r = uds.request(request)
            if r.timeout:
                print("超时无响应")
            else:
                print(f"{r.response.hex().upper()}"
                      + (f"（NRC 0x{r.nrc:02X}）" if r.nrc is not None else "")
                      + f"  {r.elapsed_ms:.0f}ms")
        return 0
    finally:
        transport.stop()
        rec.close()
        driver.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="UDS 诊断测试工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="跑配置流程")
    p_run.add_argument("config")
    p_run.set_defaults(func=cmd_run)

    p_chat = sub.add_parser("chat", help="交互模式")
    p_chat.add_argument("--config", required=True)
    p_chat.set_defaults(func=cmd_chat)

    p_probe = sub.add_parser("probe", help="单发一条请求")
    p_probe.add_argument("--config", required=True)
    p_probe.add_argument("request", help="十六进制请求，如 22F195")
    p_probe.set_defaults(func=cmd_probe)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断。已完成的记录见 logs/ 下对应运行目录的 run.log")
        return 130


if __name__ == "__main__":
    sys.exit(main())
