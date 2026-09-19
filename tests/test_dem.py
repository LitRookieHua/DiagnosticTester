import pytest

import dem
from flow import ConfigError


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
