"""DEM 故障检测：map 表加载、五项请求、响应解析、报告生成。

map 文件与 --config 同目录、按表分文件（均可选，缺省回退显示原始值）：
dtc_map.yaml / event_map.yaml / fimfid_map.yaml / dem_dids.yaml。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from flow import ConfigError

DEFAULT_DIDS = {
    "event_current": 0x2A0D,
    "event_cycle": 0x2A0E,
    "event_history": 0x2A0F,
    "fimfid_current": 0x2A1E,
}

DTC_STATUS_BITS = [
    (0x01, "testFailed"),
    (0x02, "testFailedThisOperationCycle"),
    (0x04, "pendingDTC"),
    (0x08, "confirmedDTC"),
    (0x10, "testNotCompletedSinceLastClear"),
    (0x20, "testFailedSinceLastClear"),
    (0x40, "testNotCompletedThisOperationCycle"),
    (0x80, "warningIndicatorRequested"),
]


def _load_yaml_dict(path: Path, what: str) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name}: YAML 语法错误：{e}")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: {what} 根节点必须是映射")
    return raw


def _check_did(v, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{where}: 应为 DID（0x0001-0xFFFF 的整数），当前 {v!r}")
    if not 0x0001 <= v <= 0xFFFF:
        raise ConfigError(f"{where}: DID 超出 16 位范围：0x{v:X}")
    return v


def _check_code_key(k, where: str, nibbles: int) -> int:
    """map 表的键：int（YAML 0x 十六进制）或十六进制字符串，限 nibbles 位。"""
    if isinstance(k, bool):
        raise ConfigError(f"{where}: 键非法 {k!r}")
    if isinstance(k, int):
        code = k
    elif isinstance(k, str):
        s = k.strip().lower().removeprefix("0x")
        if not s or any(c not in "0123456789abcdef" for c in s):
            raise ConfigError(f"{where}: 键 '{k}' 不是合法十六进制")
        code = int(s, 16)
    else:
        raise ConfigError(f"{where}: 键非法 {k!r}")
    if not 0 <= code < (1 << (nibbles * 4)):
        raise ConfigError(f"{where}: 键超出 {nibbles} 位十六进制范围：0x{code:X}")
    return code


def _load_table(config_dir: Path, name: str, nibbles: int,
                min_key: int | None) -> dict:
    """发现并加载一张 map 表：.yaml 优先于 .csv；只有 .csv 时明确报未支持。"""
    yml = config_dir / f"{name}.yaml"
    csv = config_dir / f"{name}.csv"
    if yml.exists():
        path = yml
    elif csv.exists():
        raise ConfigError(f"{name}.csv：CSV 导入暂未支持，请转换为 {name}.yaml")
    else:
        return {}
    table = {}
    for k, v in _load_yaml_dict(path, name).items():
        where = f"{path.name} 键 {k!r}"
        code = _check_code_key(k, where, nibbles)
        if min_key is not None and code < min_key:
            raise ConfigError(f"{where}: 不能小于 {min_key}")
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(f"{where}: 值应为非空字符串，当前 {v!r}")
        table[code] = v
    return table


def load_maps(config_dir) -> dict:
    """加载 --config 同目录的四个可选 map 文件，返回
    {dids, dtc_map, event_map, fimfid_map}。"""
    d = Path(config_dir)
    dids = dict(DEFAULT_DIDS)
    dids_path = d / "dem_dids.yaml"
    if dids_path.exists():
        raw = _load_yaml_dict(dids_path, "dem_dids")
        unknown = set(raw) - set(DEFAULT_DIDS)
        if unknown:
            raise ConfigError(f"dem_dids.yaml: 未知字段 {sorted(unknown)}")
        for k, v in raw.items():
            dids[k] = _check_did(v, f"dem_dids.yaml.{k}")
    return {
        "dids": dids,
        "dtc_map": _load_table(d, "dtc_map", nibbles=6, min_key=None),
        "event_map": _load_table(d, "event_map", nibbles=8, min_key=1),
        "fimfid_map": _load_table(d, "fimfid_map", nibbles=8, min_key=1),
    }
