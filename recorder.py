"""三路记录：run.log 文本 + result.csv + trace.blf（python-can BLFWriter）。

frame() 会被 IsoTpTransport 的 tap 回调（pump 接收线程）与发送线程并发
调用；event() 亦会被 pump 线程的日志回调并发调用，故 BLF 与文本各用
独立锁。

重要：python-can 的 BLFWriter 把消息缓存在内存，仅 stop()（即 close()）
时落盘——不 close 则 trace.blf 完全为空。建议始终用 ``with`` 语句，
保证 Ctrl+C 等异常退出时已收到的记录仍正常落盘。
"""
from __future__ import annotations

import csv
import sys
import threading
import time
from pathlib import Path

import can

CSV_HEADER = ["seq", "step", "addressing", "request", "response", "nrc",
              "result", "elapsed_ms", "timestamp"]


def hexs(data) -> str:
    return data.hex().upper() if data else ""


class Recorder:
    """三路记录器（run.log / result.csv / trace.blf）。

    BLF 仅在 close()/with 退出时落盘（BLFWriter 内存缓存语义），
    不 close 则 BLF 内容全丢，建议 ``with Recorder(...) as r`` 使用。
    close 后 frame()/event() 静默丢弃，不抛异常。
    """

    def __init__(self, run_dir, blf_channel: int = 1):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._txt = open(self.run_dir / "run.log", "a", encoding="utf-8")
        self._csv_file = open(self.run_dir / "result.csv", "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(CSV_HEADER)
        self._blf_lock = threading.Lock()
        self._txt_lock = threading.Lock()
        self._closed = False
        try:
            self._blf = can.BLFWriter(str(self.run_dir / "trace.blf"), channel=blf_channel)
        except Exception:
            # BLF 构造失败时不能泄漏已打开的文本/CSV 句柄
            self._txt.close()
            self._csv_file.close()
            raise

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False  # 异常继续传播，仅保证已收记录落盘

    def event(self, msg: str):
        """写 run.log 文本行（pump 线程可能并发调用，带独立锁）。"""
        with self._txt_lock:
            if self._closed:
                return
            self._txt.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self._txt.flush()

    def blank(self):
        """写 run.log 空行（步骤间分隔）。"""
        with self._txt_lock:
            if self._closed:
                return
            self._txt.write("\n")
            self._txt.flush()

    def frame(self, frame, is_rx: bool, ts: float | None = None):
        msg = can.Message(timestamp=ts if ts is not None else time.time(),
                          arbitration_id=frame.can_id, data=frame.data,
                          is_extended_id=False, is_fd=frame.is_fd,
                          bitrate_switch=frame.bitrate_switch, is_rx=is_rx)
        with self._blf_lock:
            if self._closed:
                return
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
        """关闭三路文件并令 BLF 落盘。幂等：二次调用直接返回。"""
        with self._blf_lock:
            if self._closed:
                return
            self._closed = True
        # _closed 已置位：此后 frame() 在锁内看到即丢弃，stop() 无并发写入
        with self._txt_lock:
            self._txt.close()
        self._csv_file.close()
        try:
            self._blf.stop()
        except Exception as exc:
            # stop 抛错不能影响已关句柄的语义：吞掉并尽力告警到 stderr
            print(f"[recorder] BLF stop 失败，trace.blf 可能不完整: {exc!r}",
                  file=sys.stderr)
