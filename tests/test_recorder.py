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
