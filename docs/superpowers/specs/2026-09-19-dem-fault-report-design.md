# diag 工具新增 dem 子命令：故障检测报告

日期：2026-09-19
状态：已确认

## 背景与目标

排查 ECU 故障时需要逐条手发 19 02 / 22 2A0x 请求并人工比对十六进制响应。
新增 `dem` 子命令：一次连上 ECU 拉取五项故障信息，按 map 表翻译成
「编号 + 名称 + 状态」的报告。

五项请求（DID 全部可配置，默认值如下）：

| # | 内容 | 请求 |
| --- | --- | --- |
| 1 | DTC 故障 | `19 02 0F` |
| 2 | 当前 event | `22 2A0D` |
| 3 | 本周期 event | `22 2A0E` |
| 4 | 历史 event | `22 2A0F` |
| 5 | 当前 fimfid | `22 2A1D` |

通用设计：map 表按项目目录可选提供，未配置的项目回退显示原始值。

## 1. map 文件：每项目一套，按表分文件

```
configs/<项目>/
  rx.yaml             # 主配置，不动
  dtc_map.yaml        # 可选
  event_map.yaml      # 可选
  fimfid_map.yaml     # 可选
  dem_dids.yaml       # 可选，DID 覆盖
```

```yaml
# dtc_map.yaml —— 根节点直接是映射，无外层包裹
0xEA0788: DemDTC_U2A0788_CAN_ACAN_Busoff
0xEA0790: DemDTC_U2A0790_CAN_ACAN_Busoff

# event_map.yaml —— 键从 1 开始，对应位图中第 N 位
1: DemEvent_HCTDTC_1
2: DemEvent_HCTDTC_2

# fimfid_map.yaml —— 键规则同 event_map
1: FiM_Function_Xxx

# dem_dids.yaml —— 全部可选，不配用默认值
event_current: 0x2A0D
event_cycle: 0x2A0E
event_history: 0x2A0F
fimfid_current: 0x2A1D
```

- **发现规则**：`dem` 子命令按 `--config` 所在目录逐个找这四个文件，
  哪个存在加载哪个；不存在用空表/默认 DID。
- **校验**（沿用加载期未知键即报错原则）：
  - `dtc_map` 键必须是合法 3 字节十六进制（YAML 0x 前缀整数）；
  - `event_map` / `fimfid_map` 键必须是 ≥1 的整数；
  - 三张表的值必须是非空字符串；
  - `dem_dids` 只允许上列四个键，值为合法 DID。
- **CSV 预留**：`load_maps()` 按文件后缀分发（`.yaml` / `.csv`），
  同目录下 `dtc_map.csv` 同样能被发现；`.yaml` 与 `.csv` 并存时 `.yaml` 优先。
  本期只实现 YAML 分支，
  CSV 格式定为两列 `code,name`（首行表头可选），后续加 `_load_csv` 即可。

## 2. 请求执行与解析

全部在**默认会话**直接发送（读 19/22 服务不需要扩展会话与安全解锁），
复用 `open_session`（driver → Recorder → ISO-TP → UDS）。

逐项执行，单项失败不中断：

| 请求 | 响应 | 解析 |
| --- | --- | --- |
| `19 02 0F` | `59 02 <可用掩码> <DTC×3B><status×1B>…` 重复 | 每个 DTC 查 `dtc_map`；status 八位解码 |
| `22 <event_did>` ×3 | `62 <DID> <位图字节…>` | 多字节位图：byte0 bit0 = 1 号，byte0 bit1 = 2 号，byte1 bit0 = 9 号…；每个置位输出 `编号: 名称` |
| `22 <fimfid_did>` | 同上 | 同上，查 `fimfid_map` |

**DTC status 八位解码**（ISO 14229-1）：

```
bit0 testFailed                     bit4 testNotCompletedSinceLastClear
bit1 testFailedThisOperationCycle   bit5 testFailedSinceLastClear
bit2 pendingDTC                     bit6 testNotCompletedThisOperationCycle
bit3 confirmedDTC                   bit7 warningIndicatorRequested
```

**回退规则**：DTC 不在 `dtc_map` → `EA0788（未在 dtc_map 中）`；
event/fimfid 编号不在 map → `13:（未在 event_map 中）`。

## 3. 报告输出与退出码

控制台与 run.log 双写（报告行经 `Recorder.event` 落盘）：

```
===== DEM 故障报告 =====
[DTC 故障]（19 02 0F）
  EA0788  DemDTC_U2A0788_CAN_ACAN_Busoff   0x2F  testFailed|testFailedThisOperationCycle|pendingDTC|confirmedDTC|testFailedSinceLastClear
  （无 DTC 时显示：无故障记录）

[当前 event]（22 2A0D）
  1: DemEvent_HCTDTC_1
  6: DemEvent_HCTDTC_6

[本周期 event]（22 2A0E）
  （无置位 event）

[历史 event]（22 2A0F）
  3: DemEvent_HCTDTC_3
  13:（未在 event_map 中）

[当前 fimfid]（22 2A1D）
  2: FiM_Function_Xxx
=====================
```

**错误处理**：

- 单项超时/负响应 → 该项显示 `(超时无响应)` 或 `(NRC 0x__)`，继续下一项；
- `19` 响应 DTC+status 不是 4 字节整数倍 → 显示原始数据 + 警告行；
- 位图 DID 响应无数据字节 → 显示 `(空位图)`。

**退出码**：五项全部正响应 → 0（**是否存在故障不影响退出码**）；
任一项超时/负响应/解析失败 → 1。与 probe 命令惯例一致。

## 4. 代码改动范围

- 新增 `dem.py`（约 200 行）：
  - `load_maps(config_dir) -> dict`：四文件发现、校验、后缀分发；
  - `parse_1902(response)`：→ (可用掩码, [(dtc, status), …])，长度异常抛 ValueError；
  - `parse_bitmap(payload)`：→ 置位编号列表（从 1 起）；
  - `decode_status(status)`：→ 八位状态名列表；
  - `run_dem(uds, maps, emit)`：五项执行 + 报告生成。
- `diag.py`：`dem` 子命令（复用 `open_session`，约 30 行），README 命令表加一行。
- 示例 map：`configs/` 根放一套（与 example_mock.yaml 同目录，免迁移）。

## 5. 测试

- `tests/test_dem.py`：
  - map 加载校验（各表正常 / 键非法 / 未知键 / CSV 后缀占位）；
  - 位图解析（单字节 0x21 → [1,6]、多字节、全 0、超 map 范围编号）；
  - `parse_1902`（正常多 DTC、长度异常、status 各 bit 组合）；
  - 回退显示（无 map 时原始值文案）。
- `tests/test_diag_cli.py` 增补：mock driver 下 `main(["dem", ...])`
  端到端，验证报告文本与退出码（全正响应 0 / 含超时 1）。

## 6. 验证

- `python -m pytest tests/ -v` 全绿；
- 免硬件冒烟：example_mock 配置跑 `python diag.py dem --config configs/example_mock.yaml`；
- 真机验证（用户执行）：目标项目放好 map 文件后跑 dem，对照 DLT/串口日志
  核对 DTC 名称与 status 位。
