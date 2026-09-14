import can

from can_driver import CanFrame
from recorder import Recorder
from uds import UdsResult


def test_recorder_writes_three_files(tmp_path):
    r = Recorder(tmp_path, blf_channel=1)
    r.event("流程开始")
    r.frame(CanFrame(0x70C, b"\x03\x22\xf1\x95"), is_rx=False)
    r.step(1, "读DID F195", "physical", "22F195",
           UdsResult(response=bytes.fromhex("62F1950102"), elapsed_ms=12.3), "PASS")
    r.close()

    log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "流程开始" in log_text

    csv_text = (tmp_path / "result.csv").read_text(encoding="utf-8")
    assert "22F195" in csv_text
    assert "62F1950102" in csv_text
    assert "PASS" in csv_text
    assert "读DID F195" in csv_text

    msgs = list(can.BLFReader(str(tmp_path / "trace.blf")))
    assert len(msgs) == 1
    assert msgs[0].arbitration_id == 0x70C
    assert msgs[0].data == b"\x03\x22\xf1\x95"
    assert msgs[0].is_rx is False


def test_recorder_nrc_column(tmp_path):
    r = Recorder(tmp_path)
    r.step(1, "s", "physical", "22F195",
           UdsResult(response=bytes.fromhex("7F2231"), nrc=0x31, elapsed_ms=5.0), "FAIL")
    r.close()
    assert "0x31" in (tmp_path / "result.csv").read_text(encoding="utf-8")


def test_close_idempotent(tmp_path):
    r = Recorder(tmp_path)
    r.event("开始")
    r.frame(CanFrame(0x70C, b"\x02\x10\x03"), is_rx=False)
    r.close()
    r.close()  # 二次 close 必须幂等，不抛异常

    msgs = list(can.BLFReader(str(tmp_path / "trace.blf")))
    assert len(msgs) == 1


def test_frame_after_close_dropped(tmp_path):
    r = Recorder(tmp_path)
    r.frame(CanFrame(0x70C, b"\x02\x10\x03"), is_rx=False)
    r.close()
    before = list(can.BLFReader(str(tmp_path / "trace.blf")))
    assert len(before) == 1

    # close 后再写帧：静默丢弃，不抛异常，BLF 内容不变
    r.frame(CanFrame(0x78C, b"\x06\x50\x03\x00\x32\x01\xf4"), is_rx=True)

    after = list(can.BLFReader(str(tmp_path / "trace.blf")))
    assert len(after) == 1
    assert after[0].arbitration_id == 0x70C


def test_fd_frame_roundtrip(tmp_path):
    r = Recorder(tmp_path)
    payload = bytes(range(64))
    r.frame(CanFrame(0x78C, payload, is_fd=True, bitrate_switch=True), is_rx=True)
    r.close()

    msgs = list(can.BLFReader(str(tmp_path / "trace.blf")))
    assert len(msgs) == 1
    assert msgs[0].is_fd is True
    assert msgs[0].bitrate_switch is True
    assert msgs[0].arbitration_id == 0x78C
    assert bytes(msgs[0].data) == payload


def test_context_manager(tmp_path):
    with Recorder(tmp_path) as r:
        r.event("with 块内")
        r.frame(CanFrame(0x70C, b"\x03\x22\xf1\x95"), is_rx=False)
        r.frame(CanFrame(0x78C, b"\x06\x62\xf1\x95\x01\x02\x03"), is_rx=True)
        r.step(1, "读DID F195", "physical", "22F195",
               UdsResult(response=bytes.fromhex("62F1950102"), elapsed_ms=12.3), "PASS")
    # 退出 with 后三文件齐备且 BLF 已落盘可读回
    assert (tmp_path / "run.log").exists()
    assert (tmp_path / "result.csv").exists()
    msgs = list(can.BLFReader(str(tmp_path / "trace.blf")))
    assert len(msgs) == 2
