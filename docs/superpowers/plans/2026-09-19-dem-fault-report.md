# dem 故障检测子命令 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** diag 工具新增 `dem` 子命令：一次拉取五项故障信息（19 02 0F DTC、22 2A0D/E/F event、22 2A1E fimfid），按项目 map 表翻译成「编号 + 名称 + 状态」报告。

**Architecture:** 新增独立 `dem.py` 模块（map 加载校验、响应解析、报告生成，纯函数为主），`diag.py` 加 `dem` 子命令复用现有 `open_session`（driver → Recorder → ISO-TP → UDS）。map 文件按表分文件放 `--config` 同目录，缺省回退原始值显示。

**Tech Stack:** Python 3.10+、PyYAML、pytest（复用 tests/ 现有 mock driver 体系）。

**设计文档:** `docs/superpowers/specs/2026-09-19-dem-fault-report-design.md`

---

## 背景：现有代码要点（实现者必读）

- `diag.py`：CLI 入口。子命令模式：`sub.add_parser(...)` + `set_defaults(func=cmd_xxx)`。
  `open_session(cfg, project, prefix)` 是上下文管理器，yield `(driver, rec, transport, uds)`，
  退出时自动收尾。`config_project(config_path)` 取日志前缀项目名。
- `flow.py`：`ConfigError` 是所有加载期校验错误的类型（CLI 捕获后退出码 2）。
- `uds.py`：`UdsClient.request(payload) -> UdsResult`，`UdsResult(response, nrc, timeout, elapsed_ms, pendings)`。
  超时 `r.timeout=True`；负响应 `r.nrc=<int>`。
- `recorder.py`：`rec.event(msg)` 写 run.log；`rec.step(...)` 写 result.csv。
- `tests/`：`conftest.py` 已把仓库根加进 sys.path（`import dem` 直接可用）；
  `tests/mock_driver.py` 的 `MockCanDriver` 有 `push_rx(can_id, data)` 和 `on_send` 回调。
- `tests/test_diag_cli.py` 的 `run_cli` fixture：`runner(argv, ecu=None, cfg_name=...)`
  把配置写到 `tmp_path`（或 `tmp_path/<cfg_name>` 子目录）并执行 `diag.main`。
  map 文件发现走 `--config` 所在目录，所以在测试里把 map 写到同一目录即可。

**ISO 14229 DTC status 八位定义**（`dem.py` 的 `DTC_STATUS_BITS` 用）：

```
bit0 testFailed                     bit4 testNotCompletedSinceLastClear
bit1 testFailedThisOperationCycle   bit5 testFailedSinceLastClear
bit2 pendingDTC                     bit6 testNotCompletedThisOperationCycle
bit3 confirmedDTC                   bit7 warningIndicatorRequested
```

**位图编号规则**：byte0 bit0 = 1 号、byte0 bit1 = 2 号、byte1 bit0 = 9 号（多字节位图，从 1 起）。

---

### Task 1: dem.py map 表加载与校验

**Files:**
- Create: `dem.py`
- Create: `tests/test_dem.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_dem.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd diag && python -m pytest tests/test_dem.py -v`
Expected: 全部 FAIL/ERROR（`ModuleNotFoundError: No module named 'dem'`）

- [ ] **Step 3: 写最小实现**

新建 `dem.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd diag && python -m pytest tests/test_dem.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add dem.py tests/test_dem.py
git commit -m "feat(dem): map 表加载与校验（dtc/event/fimfid/dem_dids 分文件，YAML 主格式）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 19 02 DTC 响应解析与状态解码

**Files:**
- Modify: `dem.py`（追加解析函数）
- Modify: `tests/test_dem.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_dem.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd diag && python -m pytest tests/test_dem.py -v -k "parse_1902 or decode_status"`
Expected: FAIL（`AttributeError: module 'dem' has no attribute 'parse_1902'`）

- [ ] **Step 3: 在 dem.py 追加实现**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd diag && python -m pytest tests/test_dem.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add dem.py tests/test_dem.py
git commit -m "feat(dem): 19 02 DTC 响应解析与八位状态解码

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: event/fimfid 多字节位图解析

**Files:**
- Modify: `dem.py`（追加 `parse_bitmap`）
- Modify: `tests/test_dem.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_dem.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd diag && python -m pytest tests/test_dem.py -v -k parse_bitmap`
Expected: FAIL（`AttributeError: ... no attribute 'parse_bitmap'`）

- [ ] **Step 3: 在 dem.py 追加实现**

```python
def parse_bitmap(payload: bytes) -> list[int]:
    """位图字节 → 置位编号列表（byte0 bit0=1、byte1 bit0=9，从 1 起）。"""
    nums = []
    for i, byte in enumerate(payload):
        for bit in range(8):
            if byte >> bit & 1:
                nums.append(i * 8 + bit + 1)
    return nums
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd diag && python -m pytest tests/test_dem.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add dem.py tests/test_dem.py
git commit -m "feat(dem): event/fimfid 多字节位图解析

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: run_dem 报告生成（FakeUds 单测）

**Files:**
- Modify: `dem.py`（追加 `_request`、`_dem_dtc`、`_dem_bitmap`、`run_dem`）
- Modify: `tests/test_dem.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_dem.py` 顶部 import 区补充（已有 import 下方）：

```python
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
        bytes.fromhex("19020F"): UdsResult(
            response=bytes.fromhex("5902FFEA07882F")),
        bytes.fromhex("222A0D"): UdsResult(
            response=bytes.fromhex("622A0D21")),
        bytes.fromhex("222A0E"): UdsResult(
            response=bytes.fromhex("622A0E00")),
        bytes.fromhex("222A0F"): UdsResult(
            response=bytes.fromhex("622A0F0401")),
        bytes.fromhex("222A1E"): UdsResult(
            response=bytes.fromhex("622A1E02")),
    })
```

在文件末尾追加：

```python
# ---------- run_dem ----------

def test_run_dem_full_report():
    lines = []
    assert dem.run_dem(_ok_uds(), _maps(), lines.append) == 0
    text = "\n".join(lines)
    assert "===== DEM 故障报告 =====" in text
    assert "[DTC 故障]（19 02 0F）" in text
    assert ("EA0788  DemDTC_U2A0788_CAN_ACAN_Busoff  0x2F"
            "  testFailed|testFailedThisOperationCycle|pendingDTC"
            "|confirmedDTC|testFailedSinceLastClear") in text
    assert "[当前 event]（22 2A0D）" in text
    assert "1: DemEvent_HCTDTC_1" in text
    assert "6:（未在 event_map 中）" in text
    assert "[本周期 event]（22 2A0E）" in text
    assert "（无置位 event）" in text
    assert "[历史 event]（22 2A0F）" in text
    assert "3:（未在 event_map 中）" in text
    assert "9:（未在 event_map 中）" in text
    assert "[当前 fimfid]（22 2A1E）" in text
    assert "2: FiM_Function_Xxx" in text


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
    assert text.count("(超时无响应)") == 1  # 只有 19 超时；2A0D 走 NRC 分支
    assert "(NRC 0x31)" in text
    assert "2: FiM_Function_Xxx" in text  # 后续节照常输出


def test_run_dem_bad_1902_length_shows_raw():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("19020F")] = UdsResult(
        response=bytes.fromhex("5902FFEA0788"))
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 1
    text = "\n".join(lines)
    assert "解析失败" in text
    assert "5902FFEA0788" in text


def test_run_dem_empty_bitmap():
    fake = _ok_uds()
    fake.responses[bytes.fromhex("222A0D")] = UdsResult(
        response=bytes.fromhex("622A0D"))
    lines = []
    assert dem.run_dem(fake, _maps(), lines.append) == 1
    assert "(空位图)" in "\n".join(lines)


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd diag && python -m pytest tests/test_dem.py -v -k run_dem`
Expected: FAIL（`AttributeError: module 'dem' has no attribute 'run_dem'`）

- [ ] **Step 3: 在 dem.py 追加实现**

```python
def _hex(request: bytes) -> str:
    return " ".join(f"{b:02X}" for b in request)


def _request(uds, emit, request: bytes, title: str):
    """发一项请求；返回 UdsResult，超时/负响应时返回 None（已输出失败文案）。"""
    emit(f"[{title}]（{_hex(request)}）")
    r = uds.request(request)
    if r.timeout:
        emit("  (超时无响应)")
        return None
    if r.nrc is not None:
        emit(f"  (NRC 0x{r.nrc:02X})")
        return None
    return r


def _dem_dtc(uds, maps, emit) -> bool:
    r = _request(uds, emit, bytes.fromhex("19020F"), "DTC 故障")
    if r is None:
        return False
    try:
        _mask, records = parse_1902(r.response)
    except ValueError as e:
        emit(f"  解析失败：{e}")
        emit(f"  原始响应：{r.response.hex().upper()}")
        return False
    if not records:
        emit("  无故障记录")
    for dtc, status in records:
        name = maps["dtc_map"].get(dtc, "（未在 dtc_map 中）")
        bits = "|".join(decode_status(status))
        emit(f"  {dtc:06X}  {name}  0x{status:02X}  {bits}")
    return True


def _dem_bitmap(uds, maps, emit, did_key: str, table_key: str,
                title: str, empty_label: str) -> bool:
    did = maps["dids"][did_key]
    request = bytes([0x22, did >> 8, did & 0xFF])
    r = _request(uds, emit, request, title)
    if r is None:
        return False
    if r.response[0] != 0x62 or r.response[1:3] != request[1:3]:
        emit(f"  响应不是预期的 62 {did:04X}：{r.response.hex().upper()}")
        return False
    payload = r.response[3:]
    if not payload:
        emit("  (空位图)")
        return False
    nums = parse_bitmap(payload)
    if not nums:
        emit(f"  {empty_label}")
    for n in nums:
        emit(f"  {n}: {maps[table_key].get(n, f'（未在 {table_key} 中）')}")
    return True


def run_dem(uds, maps, emit) -> int:
    """执行五项故障拉取，逐行 emit 报告；返回退出码（全正响应 0，否则 1）。"""
    emit("===== DEM 故障报告 =====")
    ok = True
    if not _dem_dtc(uds, maps, emit):
        ok = False
    for did_key, title in [("event_current", "当前 event"),
                           ("event_cycle", "本周期 event"),
                           ("event_history", "历史 event")]:
        if not _dem_bitmap(uds, maps, emit, did_key, "event_map",
                           title, "（无置位 event）"):
            ok = False
    if not _dem_bitmap(uds, maps, emit, "fimfid_current", "fimfid_map",
                       "当前 fimfid", "（无置位 fimfid）"):
        ok = False
    emit("=====================")
    return 0 if ok else 1
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd diag && python -m pytest tests/test_dem.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add dem.py tests/test_dem.py
git commit -m "feat(dem): 五项故障拉取与报告生成 run_dem

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: diag.py dem 子命令 + CLI 端到端测试

**Files:**
- Modify: `diag.py`（新增 `cmd_dem` + 子命令注册）
- Modify: `tests/test_diag_cli.py`（追加 dem 测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_diag_cli.py` 末尾追加：

```python
# ---------- dem ----------

DEM_RESP = {
    "19020F": "5902FFEA07882F",
    "222A0D": "622A0D21",
    "222A0E": "622A0E00",
    "222A0F": "622A0F0401",
    "222A1E": "622A1E02",
}


def _dem_ecu(mock):
    """按 DEM_RESP 脚本回帧的假 ECU。"""
    def on_send(frame):
        req = _parse_sf(frame.data)
        resp = DEM_RESP.get(req.hex().upper())
        if resp is not None:
            mock.push_rx(0x78C, _sf_rx(bytes.fromhex(resp)))
    return on_send


def test_dem_report(run_cli, tmp_path):
    (tmp_path / "dtc_map.yaml").write_text(
        "0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff\n", encoding="utf-8")
    (tmp_path / "event_map.yaml").write_text(
        "1: DemEvent_HCTDTC_1\n", encoding="utf-8")
    (tmp_path / "fimfid_map.yaml").write_text(
        "2: FiM_Function_Xxx\n", encoding="utf-8")
    mock = MockCanDriver()
    mock.on_send = _dem_ecu(mock)
    code, out = run_cli(["dem"], ecu=mock)
    assert code == 0
    assert "DemDTC_U2A0788_CAN_ACAN_Busoff" in out
    assert "1: DemEvent_HCTDTC_1" in out
    assert "6:（未在 event_map 中）" in out
    assert "（无置位 event）" in out
    assert "2: FiM_Function_Xxx" in out


def test_dem_all_timeout_exit_1(run_cli):
    mock = MockCanDriver()  # 不回帧
    code, out = run_cli(["dem"], ecu=mock)
    assert code == 1
    assert out.count("(超时无响应)") == 5


def test_dem_log_dir_uses_project(run_cli, tmp_path):
    mock = MockCanDriver()  # 无响应无妨，目录已建
    run_cli(["dem"], ecu=mock, cfg_name="CMC_RX/rx.yaml")
    assert any(p.is_dir() and p.name.startswith("CMC_RX_dem_")
               for p in tmp_path.iterdir())
```

注意：`run_cli` 的 runner 会把 argv 里的 `"tc"` 替换成 testcase 路径；dem 不需要
testcase，直接传 `["dem"]`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd diag && python -m pytest tests/test_diag_cli.py -v -k dem`
Expected: FAIL（argparse 报 `invalid choice: 'dem'`，SystemExit(2)）

- [ ] **Step 3: 修改 diag.py**

在 import 区（`from flow import ...` 之后）加：

```python
from dem import load_maps, run_dem
```

在 `cmd_chat` 之前（`cmd_unlock` 之后）加：

```python
def cmd_dem(args):
    cfg = load_config(args.config)
    maps = load_maps(Path(args.config).parent)
    with open_session(cfg, config_project(args.config), "dem") as (_driver, rec, _transport, uds):
        def emit(msg):
            print(msg)
            rec.event(msg)
        return run_dem(uds, maps, emit)
```

在 `main()` 的子命令注册区（`p_unlock` 之后）加：

```python
    p_dem = sub.add_parser("dem", help="拉取 DTC/event/fimfid 故障报告")
    p_dem.add_argument("--config", required=True)
    p_dem.set_defaults(func=cmd_dem)
```

同时在模块 docstring 的用法列表末尾加一行：

```
  python diag.py dem --config <config.yaml>                  故障检测报告（DTC/event/fimfid）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd diag && python -m pytest tests/test_diag_cli.py -v`
Expected: 全部 PASS（含原有测试，无回归）

- [ ] **Step 5: 提交**

```bash
git add diag.py tests/test_diag_cli.py
git commit -m "feat(dem): diag.py dem 子命令（复用 open_session，报告双写控制台与 run.log）

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 示例 map、README、设计文档对齐与全量验证

**Files:**
- Create: `configs/dtc_map.yaml`、`configs/event_map.yaml`、`configs/fimfid_map.yaml`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-19-dem-fault-report-design.md`（示例 map 位置一处）

说明：`example_mock.yaml` 在 configs/ 根而非子目录，示例 map 放 configs/ 根与之
同目录即可被发现，不迁移现有文件结构（避免 README 多处路径改动）。

- [ ] **Step 1: 创建示例 map 文件**

`configs/dtc_map.yaml`：

```yaml
# DTC map 示例：键 = 3 字节 DTC，值 = Dem 中的名称
# dem 子命令按 --config 同目录发现本文件；删除即回退显示原始 DTC 值
0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff
```

`configs/event_map.yaml`：

```yaml
# event map 示例：键从 1 开始，对应 22 2A0D/2A0E/2A0F 位图中第 N 位
1: DemEvent_HCTDTC_1
2: DemEvent_HCTDTC_2
3: DemEvent_HCTDTC_3
4: DemEvent_HCTDTC_4
5: DemEvent_HCTDTC_5
6: DemEvent_HCTDTC_6
7: DemEvent_HCTDTC_7
8: DemEvent_HCTDTC_8
```

`configs/fimfid_map.yaml`：

```yaml
# fimfid map 示例：键规则同 event_map，对应 22 2A1E 位图
1: FiM_Function_Xxx
```

- [ ] **Step 2: 更新 README**

命令一览表追加一行：

```markdown
| `dem` | 故障检测报告（DTC/event/fimfid） | `python diag.py dem --config configs/CMC_RX/rx.yaml` |
```

目录结构小节在 `logs/` 行之前加一段：

```markdown
    configs/<项目>/dtc_map.yaml    # 可选：DTC → 名称（dem 子命令用）
    event_map.yaml / fimfid_map.yaml / dem_dids.yaml   # 同左，均可选
```

- [ ] **Step 3: 设计文档对齐**

`docs/superpowers/specs/2026-09-19-dem-fault-report-design.md` 第 4 节中
「示例 map：`configs/example_mock/` 下放一套」改为
「示例 map：`configs/` 根放一套（与 example_mock.yaml 同目录，免迁移）」。

- [ ] **Step 4: 全量测试 + 免硬件冒烟**

Run: `cd diag && python -m pytest tests/ -v`
Expected: 全部 PASS

Run: `cd diag && python diag.py dem --config configs/example_mock.yaml; echo "exit=$?"`
Expected: 打印五节报告，每节 `(超时无响应)`（mock 无响应），`exit=1`。
命令本身不崩溃、日志目录 `logs/example_mock_dem_<时间戳>/` 生成。

- [ ] **Step 5: 提交**

```bash
git add configs/dtc_map.yaml configs/event_map.yaml configs/fimfid_map.yaml README.md docs/superpowers/specs/2026-09-19-dem-fault-report-design.md
git commit -m "feat(dem): 示例 map 文件、README 命令与目录说明

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 完成标准

- `python -m pytest tests/ -v` 全绿
- `python diag.py dem --config configs/example_mock.yaml` 免硬件跑通（超时报告 + exit 1）
- 真机验证（用户执行）：目标项目 map 文件就位后 `dem` 报告的 DTC 名称/status 位
  与 DLT 日志对照一致
