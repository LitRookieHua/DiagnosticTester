import pytest

import dem
from flow import ConfigError
from uds import UdsResult


class FakeUds:
    """按请求字节脚本化响应的假 UdsClient；未脚本化的请求返回超时。"""

    def __init__(self, responses: dict):
        self.responses = dict(responses)

    def request(self, payload, timeout_ms=None):
        return self.responses.get(payload, UdsResult(timeout=True))


def _maps():
    return {"dids": dict(dem.DEFAULT_DIDS),
            "dtc_map": {0xEA0788: "DemDTC_U2A0788_CAN_ACAN_Busoff"},
            "event_map": {1: "DemEvent_HCTDTC_1"},
            "fimfid_map": {2: "FiM_Function_Xxx"}}


def _ok_uds() -> FakeUds:
    return FakeUds({
        bytes.fromhex("1003"): UdsResult(
            response=bytes.fromhex("50030032")),
        bytes.fromhex("19020F"): UdsResult(
            response=bytes.fromhex("5902FFEA07882F")),
        bytes.fromhex("222A0D"): UdsResult(
            response=bytes.fromhex("622A0D21")),
        bytes.fromhex("222A0E"): UdsResult(
            response=bytes.fromhex("622A0E00")),
        bytes.fromhex("222A0F"): UdsResult(
            response=bytes.fromhex("622A0F0401")),
        bytes.fromhex("222A1D"): UdsResult(
            response=bytes.fromhex("622A1D02")),
    })


# ---------- load_maps ----------

def test_load_maps_all_files(tmp_path):
    (tmp_path / "dtc_map.yaml").write_text(
        "0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff\n", encoding="utf-8")
    (tmp_path / "event_map.yaml").write_text(
        "1: DemEvent_HCTDTC_1\n2: DemEvent_HCTDTC_2\n", encoding="utf-8")
    (tmp_path / "fimfid_map.yaml").write_text(
        "1: FiM_Function_Xxx\n", encoding="utf-8")
    (tmp_path / "dem_dids.yaml").write_text(
        "event_current: 0x2B01\n", encoding="utf-8")
    maps = dem.load_maps(tmp_path)
    assert maps["dtc_map"] == {0xEA0788: "DemDTC_U2A0788_CAN_ACAN_Busoff"}
    assert maps["event_map"] == {1: "DemEvent_HCTDTC_1", 2: "DemEvent_HCTDTC_2"}
    assert maps["fimfid_map"] == {1: "FiM_Function_Xxx"}
    assert maps["dids"]["event_current"] == 0x2B01
    assert maps["dids"]["event_cycle"] == 0x2A0E  # 未覆盖的用默认值


def test_load_maps_empty_dir_defaults(tmp_path):
    maps = dem.load_maps(tmp_path)
    assert maps["dids"] == dict(dem.DEFAULT_DIDS)
    assert maps["dtc_map"] == {}
    assert maps["event_map"] == {}
    assert maps["fimfid_map"] == {}


def test_load_maps_yaml_preferred_over_csv(tmp_path):
    # .yaml 与 .csv 并存时 .yaml 优先，.csv 被忽略
    (tmp_path / "dtc_map.yaml").write_text(
        "0xEA0788: FromYaml\n", encoding="utf-8")
    (tmp_path / "dtc_map.csv").write_text("code,name\n", encoding="utf-8")
    maps = dem.load_maps(tmp_path)
    assert maps["dtc_map"] == {0xEA0788: "FromYaml"}


def test_load_maps_csv_only_rejected(tmp_path):
    # 只有 .csv：CSV 导入本期未实现，明确报错而不是静默忽略
    (tmp_path / "dtc_map.csv").write_text("code,name\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="CSV"):
        dem.load_maps(tmp_path)


def test_load_maps_dem_dids_unknown_key(tmp_path):
    (tmp_path / "dem_dids.yaml").write_text(
        "event_xxx: 0x2B01\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="未知字段"):
        dem.load_maps(tmp_path)


def test_load_maps_dem_dids_bad_value(tmp_path):
    (tmp_path / "dem_dids.yaml").write_text(
        "event_current: 0x10000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="DID"):
        dem.load_maps(tmp_path)


def test_load_maps_dtc_key_too_big(tmp_path):
    (tmp_path / "dtc_map.yaml").write_text(
        "0x1000000: TooBig\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        dem.load_maps(tmp_path)


def test_load_maps_event_key_below_one(tmp_path):
    (tmp_path / "event_map.yaml").write_text("0: Zero\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="小于 1"):
        dem.load_maps(tmp_path)


def test_load_maps_empty_value_rejected(tmp_path):
    (tmp_path / "event_map.yaml").write_text('1: ""\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="非空字符串"):
        dem.load_maps(tmp_path)


def test_load_maps_string_hex_key(tmp_path):
    # 字符串键（0x 前缀可选）与 int 键等价归一化
    (tmp_path / "dtc_map.yaml").write_text(
        '"EA0790": ByStringKey\n', encoding="utf-8")
    (tmp_path / "event_map.yaml").write_text(
        '"0x2": ByStringKey\n', encoding="utf-8")
    maps = dem.load_maps(tmp_path)
    assert maps["dtc_map"] == {0xEA0790: "ByStringKey"}
    assert maps["event_map"] == {2: "ByStringKey"}


def test_load_maps_bad_hex_string_key(tmp_path):
    (tmp_path / "dtc_map.yaml").write_text(
        '"XYZ": Bad\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="不是合法十六进制"):
        dem.load_maps(tmp_path)


def test_load_maps_duplicate_normalized_key(tmp_path):
    # int 键与等值字符串键视为重复，必须报错而不是静默覆盖
    (tmp_path / "dtc_map.yaml").write_text(
        '0xEA0788: First\n"EA0788": Second\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="重复键"):
        dem.load_maps(tmp_path)


def test_load_maps_identical_duplicate_key(tmp_path):
    # PyYAML 默认会静默折叠字面重复键（后者覆盖前者），必须显式报错
    (tmp_path / "dtc_map.yaml").write_text(
        "0xEA0788: First\n0xEA0788: Second\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="重复键"):
        dem.load_maps(tmp_path)


def test_load_maps_gbk_file_rejected(tmp_path):
    p = tmp_path / "dtc_map.yaml"
    p.write_bytes("0xEA0788: 名字\n".encode("gbk"))  # 非 UTF-8
    with pytest.raises(ConfigError):
        dem.load_maps(tmp_path)


# ---------- parse_1902 / decode_status ----------

def test_parse_1902_single():
    mask, records = dem.parse_1902(bytes.fromhex("5902FFEA07882F"))
    assert mask == 0xFF
    assert records == [(0xEA0788, 0x2F)]


def test_parse_1902_multi():
    mask, records = dem.parse_1902(bytes.fromhex("5902FFEA07882FEA079001"))
    assert records == [(0xEA0788, 0x2F), (0xEA0790, 0x01)]


def test_parse_1902_no_records():
    mask, records = dem.parse_1902(bytes.fromhex("5902FF"))
    assert mask == 0xFF
    assert records == []


def test_parse_1902_odd_length_raises():
    with pytest.raises(ValueError):
        dem.parse_1902(bytes.fromhex("5902FFEA0788"))


def test_parse_1902_bad_sid_raises():
    with pytest.raises(ValueError):
        dem.parse_1902(bytes.fromhex("6202FF"))


def test_decode_status():
    assert dem.decode_status(0x2F) == [
        "testFailed", "testFailedThisOperationCycle",
        "pendingDTC", "confirmedDTC", "testFailedSinceLastClear"]
    assert dem.decode_status(0x00) == []
    assert dem.decode_status(0x09) == ["testFailed", "confirmedDTC"]
    assert dem.decode_status(0xD0) == ["testNotCompletedSinceLastClear",
                                       "testNotCompletedThisOperationCycle",
                                       "warningIndicatorRequested"]


# ---------- parse_bitmap ----------

def test_parse_bitmap_single_byte():
    assert dem.parse_bitmap(bytes([0x21])) == [1, 6]


def test_parse_bitmap_multi_byte():
    assert dem.parse_bitmap(bytes([0x00, 0x01])) == [9]
    assert dem.parse_bitmap(bytes([0x04, 0x01])) == [3, 9]


def test_parse_bitmap_all_bits():
    assert dem.parse_bitmap(bytes([0xFF])) == list(range(1, 9))


def test_parse_bitmap_zero_and_empty():
    assert dem.parse_bitmap(bytes([0x00])) == []
    assert dem.parse_bitmap(b"") == []


# ---------- run_dem ----------

def test_run_dem_full_report():
    lines = []
    assert dem.run_dem(_ok_uds(), _maps(), lines.append) == 0
    text = "\n".join(lines)
    assert "===== DEM 故障报告 =====" in text
    assert "[DTC 故障]" in text
    assert "UDS TX: 19 02 0F" in text
    assert "UDS RX: 59 02 FF EA 07 88 2F" in text
    assert ("EA0788  DemDTC_U2A0788_CAN_ACAN_Busoff  0x2F"
            "  testFailed|testFailedThisOperationCycle|pendingDTC"
            "|confirmedDTC|testFailedSinceLastClear") in text
    assert "[当前 event]" in text
    assert "UDS TX: 22 2A 0D" in text
    assert "1: DemEvent_HCTDTC_1" in text
    assert "6:（未在 event_map 中）" in text
    assert "[本周期 event]" in text
    assert "（无置位 event）" in text
    assert "[历史 event]" in text
    assert "3:（未在 event_map 中）" in text
    assert "9:（未在 event_map 中）" in text
    assert "[当前 fimfid]" in text
    assert "2: FiM_Function_Xxx" in text
    # 节间空行：五节之间恰好 4 个空行，收尾线紧跟最后一节
    assert lines.count("") == 4
    assert lines[-1] == "====================="


def test_run_dem_no_dtc_records():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("19020F")] = UdsResult(
        response=bytes.fromhex("5902FF"))
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 0
    assert "无故障记录" in "\n".join(lines)


def test_run_dem_timeout_and_nrc_do_not_stop_report():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("19020F")] = UdsResult(timeout=True)
    fake.responses[bytes.fromhex("222A0D")] = UdsResult(
        response=bytes.fromhex("7F2231"), nrc=0x31)
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 1
    text = "\n".join(lines)
    assert text.count("UDS RX: (timeout)") == 1  # 只有 19 超时；2A0D 走 NRC 分支
    assert "UDS RX: 7F 22 31" in text
    assert "2: FiM_Function_Xxx" in text  # 后续节照常输出


def test_run_dem_bad_1902_length_shows_raw():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("19020F")] = UdsResult(
        response=bytes.fromhex("5902FFEA0788"))
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 1
    text = "\n".join(lines)
    assert "解析失败" in text
    assert "UDS RX: 59 02 FF EA 07 88" in text


def test_run_dem_empty_bitmap():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("222A0D")] = UdsResult(
        response=bytes.fromhex("622A0D"))
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 1
    assert "(空位图)" in "\n".join(lines)


def test_run_dem_wrong_did_echo():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("222A0D")] = UdsResult(
        response=bytes.fromhex("622B0101"))
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 1
    assert "响应不是预期的 62 2A0D" in "\n".join(lines)
    assert "UDS RX: 62 2B 01 01" in "\n".join(lines)


def test_run_dem_without_maps_shows_raw():
    fake = _ok_uds()
    maps = {"dids": dict(dem.DEFAULT_DIDS), "dtc_map": {},
            "event_map": {}, "fimfid_map": {}}
    lines = []
    assert dem.run_dem(fake, maps, lines.append) == 0
    text = "\n".join(lines)
    assert "EA0788  （未在 dtc_map 中）" in text
    assert "1:（未在 event_map 中）" in text
    assert "2:（未在 fimfid_map 中）" in text
