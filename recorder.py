"""三路记录：run.log 文本 + result.csv + trace.blf（python-can BLFWriter）。

BLF 写入带锁：frame() 会被 IsoTpTransport 的 tap 回调（pump 接收线程）
与发送线程并发调用。
"""
from __future__ import annotations

import csv
import threading
import time
from pathlib import Path

import can

CSV_HEADER = ["seq", "step", "addressing", "request", "response", "nrc",
              "result", "elapsed_ms", "timestamp"]


def hexs(data) -> str:
    return data.hex().upper() if data else ""


class Recorder:
    def __init__(self, run_dir, blf_channel: int = 1):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._txt = open(self.run_dir / "run.log", "a", encoding="utf-8")
        self._csv_file = open(self.run_dir / "result.csv", "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(CSV_HEADER)
        self._blf = can.BLFWriter(str(self.run_dir / "trace.blf"), channel=blf_channel)
        self._blf_lock = threading.Lock()

    def event(self, msg: str):
        self._txt.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self._txt.flush()

    def frame(self, frame, is_rx: bool, ts: float | None = None):
        msg = can.Message(timestamp=ts if ts is not None else time.time(),
                          arbitration_id=frame.can_id, data=frame.data,
                          is_extended_id=False, is_fd=frame.is_fd,
                          bitrate_switch=frame.bitrate_switch, is_rx=is_rx)
        with self._blf_lock:
            # python-can 4.x：写入接口是 on_message_received（无 log_message）
            self._blf.on_message_received(msg)

    def step(self, seq, name, addressing, request_hex, result, verdict):
        self._csv.writerow([seq, name, addressing, request_hex,
                            hexs(result.response),
                            f"0x{result.nrc:02X}" if result.nrc is not None else "",
                            verdict, f"{result.elapsed_ms:.1f}",
                            time.strftime("%Y-%m-%d %H:%M:%S")])
        self._csv_file.flush()

    def close(self):
        self._txt.close()
        self._csv_file.close()
        with self._blf_lock:
            self._blf.stop()
