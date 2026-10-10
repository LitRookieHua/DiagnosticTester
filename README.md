# 诊断测试工具（DiagnosticTester）

周立功 / 创芯 USB-CANFD 卡上的 UDS 诊断测试工具。经典 CAN / CAN FD 均支持，
按 YAML 测试用例收发诊断数据，文本/CSV/BLF 三路落盘，支持 0x27 安全解锁。

## 快速上手

    python -m pip install -r requirements.txt
    python DiagnosticTester.py run testcase/example/example.yaml --config configs/example.yaml

前提：ZLG 卡需 ZLGCAN SDK 按 `vendor/zlgcan/README.md` 组装（创芯卡无需，见下）、CAN 卡已插、
目标 ECU 已上电；testcase/ 不入仓库，首次使用需自备用例文件。免硬件体验可先跑
`python DiagnosticTester.py run testcase/example_mock/example_mock.yaml --config configs/example_mock.yaml`。

### 创芯卡（CX-USBCANFD）

配置 `can.device: CX-USBCANFD` 即用创芯双通道卡（现成示例 `configs/example_cx.yaml`）：DLL 已随仓库内置
（`vendor/cxcanfd/`，64 位，Python 须 64 位），无需另外组装 SDK。
终端电阻无软件开关，请确认卡上拨码/跳线或外接电阻。

## 目录结构

    configs/                    # 项目配置（每个项目一个同名目录，目录名 = 配置文件名）
      n60/n60_zlg.yaml         # can / diagnostic / timing / security，
      n60/n60_cx.yaml          # _zlg=周立功卡 / _cx=创芯卡（其余项目同理）
      rx/rx_zlg.yaml / rx/rx_cx.yaml
      t1lfl1/t1lfl1_zlg.yaml / t1lfl1/t1lfl1_cx.yaml
      v2xeu/v2xeu_zlg.yaml / v2xeu/v2xeu_cx.yaml
      t75/t75_zlg.yaml / t75/t75_cx.yaml
      example.yaml / example_mock.yaml / example_cx.yaml
      <项目>/dtc_map.yaml      # 可选：DTC → 名称（dem 子命令用）
      event_map.yaml / fimfid_map.yaml / dem_dids.yaml   # 同上，均可选
                                # 示例见 configs/ 根
    testcase/                  # 测试用例（本地维护，不入仓库；目录与 configs 对应）
      <项目>/<用例>/          # 每个用例一个文件夹，yaml 与运行日志同住
        <用例>.yaml           # steps / on_fail / 可选 timing 覆盖
        <时间戳>/             # 每次运行自动生成（run 命令的日志，见「输出文件」）
    logs/                      # chat/probe/unlock/dem 的运行日志（不入仓库）

同一测试用例换 `--config` 即可跑不同项目。

## 命令一览

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `run` | 跑测试用例 | `python DiagnosticTester.py run testcase/n60/x/x.yaml --config configs/n60/n60_zlg.yaml` |
| `chat` | 交互模式，逐条发请求 | `python DiagnosticTester.py chat --config configs/n60/n60_zlg.yaml` |
| `probe` | 单发一条请求 | `python DiagnosticTester.py probe --config configs/n60/n60_zlg.yaml 22F195` |
| `unlock` | 0x27 安全解锁 | `python DiagnosticTester.py unlock --config configs/n60/n60_zlg.yaml app` |
| `dem` | 故障检测报告（DTC/event/fimfid） | `python DiagnosticTester.py dem --config configs/rx/rx_zlg.yaml` |
| `dem-map` | 手动粘贴响应离线解析 DEM map（不连 ECU） | `python DiagnosticTester.py dem-map --config configs/rx/rx_zlg.yaml` |

## 配置文件（configs/）

每个项目一份，共四节：

- **can** — 总线接入：`device`（周立功设备名 `USBCANFD-100U/200U/800U`、
  创芯 `CX-USBCANFD`，`mock` 走内置 Mock 驱动、免硬件）、`channel` 通道号、
  `fd` 是否 CAN FD、`baudrate` 仲裁域波特率；
  `data_baudrate` 为 FD 数据域波特率（可省略，默认 2000000）。
- **diagnostic** — 诊断寻址：`request_id` 请求 ID、`response_id` 响应 ID、
  `functional_id` 功能寻址 ID（可选，功能寻址步骤必需）。三个 ID 均限
  11 位标准帧范围 0x001-0x7FF。
- **timing** — 超时与节奏：`p2_ms` 单响应超时（默认 50）、`p2star_ms`
  收到 NRC 0x78 后的等待窗口（默认 5000）、`step_delay_ms` 步骤间默认
  间隔（默认 100）。三项均可省略。
- **security** — 0x27 安全解锁等级（见下）。可选。

## 测试用例（testcase/，本地维护不入仓库）

只含流程，共三节：

- **steps** — 步骤列表：每步必填 `name`，普通步骤另填 `request`（引号
  包裹的十六进制字符串，如 `"22F195"`）；可选 `expect`（期望响应前缀，
  省略则只发不判、超时不算失败）、`timeout_ms`（覆盖该步的 P2 超时）、
  `delay_after_ms`（该步收尾延时，整体覆盖 `step_delay_ms` 而非叠加）、
  `addressing`（`physical` 默认 / `functional`：发完即记、不支持
  `expect`，请求长度不得超过单帧上限——经典 7 字节、FD 62 字节）。
  另一类是安全解锁步骤：填 `security_access: <等级名>`（不可与
  `request`/`expect`/功能寻址同用，等级须在配置 security 节或为内置
  develop），执行完整 27 取种子 → 算密钥 → 发密钥序列，密钥响应
  `67+key_sub` 判 PASS。
- **on_fail** — 失败策略：`abort`（默认，首个 FAIL/TIMEOUT 即中止）或
  `continue`（跑完全部步骤）。
- **timing**（可选）— 覆盖配置的 timing 默认值，只覆盖出现的键。

## security 节（0x27 解锁配置化）

每个项目的解锁不同：等级、子功能号、算法与密钥材料都写在配置里。

```yaml
security:
  app:
    seed_sub: 0x01          # 取种子子功能号
    key_sub: 0x02           # 发密钥子功能号，必须 = seed_sub + 1
    algorithm: cmc_mask_app # 算法名（见下表）
    params:                 # 算法参数，随算法而定
      mask_table: [0xf7cbc641, ...]  # 16 个 u32
```

可用算法（种子/密钥长度由算法决定，无需配置）：

| 算法名 | 用途 | params | 密钥计算 |
| --- | --- | --- | --- |
| `aes_cmac_df_token` | N60 app/boot | `key_factor`（16B hex） | SK=CMAC(KF,DF)，Key=DF‖CMAC(SK,Seed) |
| `aes_cmac_seed` | develop、V2XEU app | `key`（16B hex）、`magic`（16B hex，可选） | Key=CMAC(key,Seed) |
| `cmc_mask_app` | RX app | `mask_table`（16×u32） | cpj_cmc seedToKeyApp |
| `cmc_mask_boot` | RX boot | `mask_table` | cpj_cmc seedToKeyBoot |
| `t1lfl1_crc16` | T1LFL1 app | 无 | CRC 式位运算（veh_t1lfl1 源码），2B→2B |
| `gac_shift_xor` | T75 app/boot | `constant`（4B hex） | uint32 移位乘法异或（cpj_gac 源码），4B→4B |

**develop（61/62，公司内部通用）内置**：所有项目自动可用，无需配置；
配置里写 `security.develop` 可覆盖。新客户算法 = 在 security.py 注册表
新增一个函数，参数仍走配置。

## run — 跑测试用例

    python DiagnosticTester.py run testcase/n60/x/x.yaml --config configs/n60/n60_zlg.yaml

输出汇总与退出码：

    PASS 3 / FAIL 0 / TIMEOUT 0 / 共 3/3     # 退出码 0
    PASS 1 / FAIL 1 / TIMEOUT 0 / 共 2/3     # 有 FAIL/TIMEOUT，退出码 1

结果同时落盘到用例 yaml 同目录下的时间戳子目录
`testcase/<项目>/<用例>/<时间戳>/`（见「输出文件」）。

## chat — 交互模式

    python DiagnosticTester.py chat --config configs/n60/n60_zlg.yaml

逐条输入十六进制请求，即时显示响应与耗时：

    diag> 22F195
    62F19520533031302E303039  16ms
    diag> unlock app
    种子 0147C899BC7BB019... 密钥 5D16181468FEDF47...
    6702  15ms
    diag> :q

- 负响应显示 NRC：`7F2231（NRC 0x31）`；无响应显示 `超时无响应`。
- `unlock <等级>` 执行安全解锁（等级来自配置 + 内置 develop），
  失败不断线可继续输入。
- `:q` / `:quit` / `exit` 退出；单条请求失败不结束会话。

## probe — 单发一条

    python DiagnosticTester.py probe --config configs/n60/n60_zlg.yaml 22F195
    响应：62F19520533031302E303039

## unlock — 安全解锁

    python DiagnosticTester.py unlock --config configs/n60/n60_zlg.yaml app

等级来自配置 security 节 + 内置 develop（61/62）。

**会话前置**：默认会话下 ECU 拒绝 0x27（NRC 0x7F），须先进扩展会话；
`boot` 级在应用（扩展）会话同样被拒（NRC 0x7E），需 boot/编程上下文。
单条命令不自动切会话——用 chat 先发 `1003` 再 `unlock`，或流程里在
解锁步骤前加进扩展会话步骤。

退出码与 `probe` 一致：0 成功、1 超时无响应、2 负响应或未知等级
（含 NRC 0x35 密钥错误）。

## dem-map — 手动粘贴响应离线解析

    python DiagnosticTester.py dem-map --config configs/t1lfl1/t1lfl1_zlg.yaml

不连 ECU、不需要 CAN 卡：逐项提示粘贴原始正响应 hex（可含空格），
用 `--config` 同目录的 dtc_map/event_map/fimfid_map 解码显示。
直接回车跳过该项。

    DTCStatus(190209): 待用户输入(直接回车即为空，直接跳过)
    CurrentEventStatus(222A0D): 待用户输入(直接回车即为空，直接跳过)
    CurrentCycleEventStatus(222A0E): 待用户输入(直接回车即为空，直接跳过)
    HistoryEventStatus(222A0F): 待用户输入(直接回车即为空，直接跳过)
    FiMFIDStatus(222A1D): 待用户输入(直接回车即为空，直接跳过)

- DTC 项须粘贴完整 `59 02 <掩码> <DTC×3B><status×1B>…` 响应
- event/fimfid 项粘贴完整 `62 <DID> <payload>` 响应或裸 payload 均可
- 输入非法或解析失败该节报错，其余节照常；退出码 0 正常、1 有失败项、
  2 配置/map 校验错误

适用场景：现场只有 CANoe 抓的原始响应、或 dem 报告里的 `UDS RX:` 行
需要复核 event/DTC 名称时。

## 输出文件

- **run**：日志生成在用例 yaml 同目录下 →
  `testcase/<项目>/<用例>/<时间戳>/`
- **chat / probe / unlock / dem**：日志生成在 `logs/` 下 →
  `logs/<项目>_<命令名>_<时间戳>/`（项目名取配置的子目录，如
  `configs/n60/n60_zlg.yaml` → n60，configs 根下的配置取文件名）

目录内容相同：

- `run.log`  人读文本流水（步骤、判定、NRC、0x78 事件、解锁种子/密钥）
- `result.csv`  UDS 级结果表（seq/步骤/请求/响应/NRC/判定/耗时/时间戳）
- `trace.blf`  总线原始帧，CANoe 可直接回放

## 退出码约定

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | run：有 FAIL/TIMEOUT；probe/unlock：ECU 超时无响应；dem：任一故障项超时/负响应/解析失败 |
| 2 | 配置错误（含未知安全等级）；probe/unlock：收到负响应；dem：配置或 map 文件校验错误 |
| 3 | 运行期异常（驱动/传输失败等） |
| 130 | Ctrl+C 中断（已落盘记录不丢） |

## 常见问题

- **`打开 USBCANFD-200U#0 失败` / `打开 CX-USBCANFD#0 失败`** — 检查 USB 连接
  与设备索引；确认没有其他程序（zcanpro、创芯官方 demo 等）占用设备。
- **`CAN FD 发送失败`** — 多为物理层问题：ECU 掉电、CAN 线未接。ECU
  上电有启动时间，重试前等几秒。
- **`错误：配置错误 ...`** — YAML 有未知字段或缺字段；错误信息会指明
  位置（如 `steps[2].request: 缺失`、`security.app.algorithm: ...`）。
- **取种子回 NRC 0x7F** — 默认会话不支持 0x27，先进扩展会话（1003）。
- **boot 级回 NRC 0x7E** — 应用会话不支持 boot 解锁，属预期行为。
- **SF 帧要求**：该 ECU 只接受恰好 8 字节的 SF 帧，工具已自动补齐，
  无需关心；写其他测试桩模拟此 ECU 时需注意。

## 测试

    python -m pytest tests/ -v
