"""DEM 故障检测：map 表加载、五项请求、响应解析、报告生成。

map 文件与 --config 同目录、按表分文件（均可选，缺省回退显示原始值）：
dtc_map.yaml / event_map.yaml / fimfid_map.yaml / dem_dids.yaml。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from flow import ConfigError, parse_hex_bytes

DEFAULT_DIDS = {
    "event_current": 0x2A0D,
    "event_cycle": 0x2A0E,
    "event_history": 0x2A0F,
    "fimfid_current": 0x2A1D,
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


class _StrictLoader(yaml.SafeLoader):
    """拒绝字面重复键的 SafeLoader（PyYAML 默认后者静默覆盖前者）。"""


def _no_dup_keys(loader, node, deep=False):
    mapping = {}
    for k_node, v_node in node.value:
        k = loader.construct_object(k_node, deep=deep)
        if k in mapping:
            raise ConfigError(
                f"重复键 {k!r}（同一文件内出现多次，后者会静默覆盖前者）")
        mapping[k] = loader.construct_object(v_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_keys)


def _load_yaml_dict(path: Path, what: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.load(text, _StrictLoader)
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path.name}: YAML 语法错误：{e}")
    except OSError as e:
        raise ConfigError(f"{path.name}: 无法读取：{e}")
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
        if code in table:
            raise ConfigError(f"{where}: 重复键（{code:X} 出现多次）")
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


def decode_status(status: int) -> list[str]:
    """DTC status 字节八位解码（ISO 14229-1）。"""
    return [name for bit, name in DTC_STATUS_BITS if status & bit]


def parse_1902(response: bytes):
    """59 02 正响应 → (状态可用掩码, [(dtc, status), ...])。

    格式：59 02 <掩码1B> <DTC×3B><status×1B>…；格式不符抛 ValueError。
    """
    if len(response) < 3 or response[0] != 0x59 or response[1] != 0x02:
        raise ValueError(f"不是 59 02 正响应：{response.hex().upper()}")
    body = response[3:]
    if len(body) % 4:
        raise ValueError(
            f"DTC 记录长度不是 4 的整数倍：{response.hex().upper()}")
    return response[2], [
        (int.from_bytes(body[i:i + 3], "big"), body[i + 3])
        for i in range(0, len(body), 4)]


def parse_bitmap(payload: bytes) -> list[int]:
    """位图字节 → 置位编号列表（byte0 bit0=1、byte1 bit0=9，从 1 起）。"""
    nums = []
    for i, byte in enumerate(payload):
        for bit in range(8):
            if byte >> bit & 1:
                nums.append(i * 8 + bit + 1)
    return nums


def _hex(request: bytes) -> str:
    return " ".join(f"{b:02X}" for b in request)


def _request(uds, emit, request: bytes, title: str):
    """发一项请求；返回 UdsResult，超时/负响应时返回 None（已输出 RX 行）。"""
    emit(f"[{title}]")
    emit(f"UDS TX: {_hex(request)}")
    r = uds.request(request)
    if r.timeout:
        emit("UDS RX: (timeout)")
        return None
    emit(f"UDS RX: {_hex(r.response)}")
    if r.nrc is not None:
        return None
    return r


def _emit_dtc_records(records, maps, emit):
    if not records:
        emit("  无故障记录")
    for dtc, status in records:
        name = maps["dtc_map"].get(dtc, "（未在 dtc_map 中）")
        bits = "|".join(decode_status(status))
        emit(f"  {dtc:06X}  {name}  0x{status:02X}  {bits}")


def _emit_bitmap_nums(payload, maps, table_key, emit, empty_label):
    nums = parse_bitmap(payload)
    if not nums:
        emit(f"  {empty_label}")
    for n in nums:
        name = maps[table_key].get(n)
        if name is None:
            emit(f"  {n}:（未在 {table_key} 中）")
        else:
            emit(f"  {n}: {name}")


def _dem_dtc(uds, maps, emit) -> bool:
    r = _request(uds, emit, bytes.fromhex("19020F"), "DTC 故障")
    if r is None:
        return False
    try:
        _mask, records = parse_1902(r.response)
    except ValueError as e:
        emit(f"  解析失败：{e}")
        return False
    _emit_dtc_records(records, maps, emit)
    return True


def _dem_bitmap(uds, maps, emit, did_key: str, table_key: str,
                title: str, empty_label: str) -> bool:
    did = maps["dids"][did_key]
    request = bytes([0x22, did >> 8, did & 0xFF])
    r = _request(uds, emit, request, title)
    if r is None:
        return False
    if r.response[0] != 0x62 or r.response[1:3] != request[1:3]:
        emit(f"  响应不是预期的 62 {did:04X}")
        return False
    payload = r.response[3:]
    if not payload:
        emit("  (空位图)")
        return False
    _emit_bitmap_nums(payload, maps, table_key, emit, empty_label)
    return True


def run_dem(uds, maps, emit) -> int:
    """执行五项故障拉取，逐行 emit 报告；返回退出码（全正响应 0，否则 1）。

    先进扩展会话（10 03）：部分 ECU 默认会话下不响应 19/22（实测 t1lfl1
    上电初期即出现），失败不阻断，继续按项读取。
    """
    emit("===== DEM 故障报告 =====")
    emit("[会话] 进扩展会话（10 03）")
    emit("UDS TX: 10 03")
    r = uds.request(bytes.fromhex("1003"))
    if r.timeout:
        emit("UDS RX: (timeout)")
    else:
        emit(f"UDS RX: {_hex(r.response)}")
    if r.timeout or r.nrc is not None:
        emit("进扩展会话失败，按默认会话继续读取")
    ok = _dem_dtc(uds, maps, emit)
    for did_key, title in [("event_current", "当前 event"),
                           ("event_cycle", "本周期 event"),
                           ("event_history", "历史 event")]:
        emit("")
        if not _dem_bitmap(uds, maps, emit, did_key, "event_map",
                           title, "（无置位 event）"):
            ok = False
    emit("")
    if not _dem_bitmap(uds, maps, emit, "fimfid_current", "fimfid_map",
                       "当前 fimfid", "（无置位 fimfid）"):
        ok = False
    emit("=====================")
    return 0 if ok else 1


# 手动 map 模式的五项：标签按现场工单口径，DID 与 dem_dids.yaml 可覆盖保持一致
_MANUAL_ITEMS = [
    ("DTCStatus(190209)", "dtc", None, None, None),
    ("CurrentEventStatus(222A0D)", "bitmap", "event_current", "event_map",
     "（无置位 event）"),
    ("CurrentCycleEventStatus(222A0E)", "bitmap", "event_cycle", "event_map",
     "（无置位 event）"),
    ("HistoryEventStatus(222A0F)", "bitmap", "event_history", "event_map",
     "（无置位 event）"),
    ("FiMFIDStatus(222A1D)", "bitmap", "fimfid_current", "fimfid_map",
     "（无置位 fimfid）"),
]


def _manual_input(label: str) -> str:
    try:
        return input(f"{label}: 待用户输入(直接回车即为空，直接跳过)").strip()
    except EOFError:
        return ""


def _manual_bitmap_payload(data: bytes, did: int) -> bytes:
    """整段 62 <DID> <payload> 响应去掉响应头；裸 payload（无 62 头）原样返回。"""
    if len(data) >= 3 and data[0] == 0x62 and data[1] == did >> 8 \
            and data[2] == did & 0xFF:
        return data[3:]
    return data


def run_dem_manual(maps, emit) -> int:
    """手动粘贴五项原始响应做离线 map 解析（不连 ECU）。

    每项提示输入完整 UDS 正响应 hex（可含空格）；直接回车跳过该节。
    返回退出码：所有已输入项解析成功（或跳过）为 0，任一输入非法/解析失败为 1。
    """
    emit("===== DEM 手动解析 =====")
    ok = True
    for label, kind, did_key, table_key, empty_label in _MANUAL_ITEMS:
        line = _manual_input(label)
        emit("")
        emit(f"[{label}]")
        if not line:
            emit("  （跳过）")
            continue
        try:
            data = parse_hex_bytes(line, label)
        except ConfigError as e:
            emit(f"  输入错误：{e}")
            ok = False
            continue
        emit(f"  输入: {_hex(data)}")
        if kind == "dtc":
            try:
                _mask, records = parse_1902(data)
            except ValueError as e:
                emit(f"  解析失败：{e}")
                ok = False
                continue
            _emit_dtc_records(records, maps, emit)
        else:
            did = maps["dids"][did_key]
            payload = _manual_bitmap_payload(data, did)
            if not payload:
                emit("  (空位图)")
                continue
            _emit_bitmap_nums(payload, maps, table_key, emit, empty_label)
    emit("=====================")
    return 0 if ok else 1
