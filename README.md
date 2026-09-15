# 诊断测试工具（diag）

周立功 USBCANFD-200U 上的 UDS 诊断测试工具。经典 CAN / CAN FD 均支持，
按 YAML 流程收发诊断数据，文本/CSV/BLF 三路落盘，支持 0x27 安全解锁。

## 快速上手

    python -m pip install -r requirements.txt
    python diag.py run configs/example.yaml        # 连真机跑示例流程

前提：ZLGCAN SDK 已按 `vendor/zlgcan/README.md` 组装、CAN 卡已插、
目标 ECU 已上电。免硬件体验可先跑 `configs/example_mock.yaml`。

## 命令一览

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `run` | 跑配置流程 | `python diag.py run configs/example.yaml` |
| `chat` | 交互模式，逐条发请求 | `python diag.py chat --config configs/example.yaml` |
| `probe` | 单发一条请求 | `python diag.py probe --config configs/example.yaml 22F195` |
| `unlock` | 0x27 安全解锁 | `python diag.py unlock --config configs/example.yaml app` |

`--config` 指定的 YAML 只用到 `can`/`diagnostic`/`timing` 三节，
`probe`/`chat`/`unlock` 不需要 steps（可不填）。

## 配置文件

流程配置为单个 YAML 文件（`configs/` 下有真机 `example.yaml` 与免硬件
`example_mock.yaml` 两个示例），共五节：

- **can** — 总线接入：`device`（周立功设备名，`mock` 走内置 Mock 驱动、
  免硬件）、`channel` 通道号、`fd` 是否 CAN FD、`baudrate` 仲裁域波特率；
  `data_baudrate` 为 FD 数据域波特率（可省略，默认 2000000）。
- **diagnostic** — 诊断寻址：`request_id` 请求 ID、`response_id` 响应 ID、
  `functional_id` 功能寻址 ID（可选，功能寻址步骤必需）。三个 ID 均限
  11 位标准帧范围 0x001-0x7FF。
- **timing** — 超时与节奏：`p2_ms` 单响应超时（默认 50）、`p2star_ms`
  收到 NRC 0x78 后的等待窗口（默认 5000，自首个 0x78 起算、后续 0x78
  不重置）、`step_delay_ms` 步骤间默认间隔（默认 100）。三项均可省略。
- **steps** — 步骤列表：每步必填 `name`，普通步骤另填 `request`（引号
  包裹的十六进制字符串，如 `"22F195"`）；可选 `expect`（期望响应前缀，
  省略则只发不判、超时不算失败）、`timeout_ms`（覆盖该步的 P2 超时，
  不约束 0x78 后的 P2* 窗口）、`delay_after_ms`（该步收尾延时，整体覆盖
  `step_delay_ms` 而非叠加）、`addressing`（`physical` 默认 /
  `functional`：发完即记、不支持 `expect`，请求长度不得超过单帧上限
  ——经典 7 字节、FD 62 字节）。
  另一类是安全解锁步骤：填 `security_access: app|boot|develop`（不可与
  `request`/`expect`/功能寻址同用），执行完整 27 取种子 → 算密钥 → 发
  密钥序列，密钥响应 `67+key_sub` 判 PASS。
- **on_fail** — 失败策略：`abort`（默认，首个 FAIL/TIMEOUT 即中止）或
  `continue`（跑完全部步骤）。

完整示例：

```yaml
can:
  device: USBCANFD-200U      # 周立功设备名；mock 为免硬件内置驱动
  channel: 0
  fd: true
  baudrate: 500000
  data_baudrate: 2000000     # 可省略，默认 2M

diagnostic:
  request_id: 0x70C          # 请求 ID
  response_id: 0x78C         # 响应 ID
  functional_id: 0x7DF       # 可选，功能寻址步骤必需

timing:
  p2_ms: 50                  # 可省略，以下均有默认值
  p2star_ms: 5000
  step_delay_ms: 100

steps:
  - name: 进扩展会话
    request: "1003"
    expect: "5003"
  - name: 解锁 app            # 安全解锁步骤（可选）
    security_access: app
  - name: 读 DID F190（VIN）
    request: "22F190"
    expect: "62F190"
    timeout_ms: 2000         # 该步单独覆盖 P2
  - name: 广播 TesterPresent  # 功能寻址：发完即记不判
    request: "3E80"
    addressing: functional
  - name: 回默认会话
    request: "1001"
    expect: "5001"
    delay_after_ms: 500      # 该步收尾延时

on_fail: abort               # abort（默认）/ continue
```

未知字段（含各嵌套节内拼错的键）在加载期直接报错，不会静默回落默认值。

## run — 跑流程

    python diag.py run configs/example.yaml

输出汇总与退出码：

    PASS 3 / FAIL 0 / TIMEOUT 0 / 共 3/3     # 退出码 0
    PASS 1 / FAIL 1 / TIMEOUT 0 / 共 2/3     # 有 FAIL/TIMEOUT，退出码 1

结果同时落盘到 `logs/<流程名>_<时间戳>/`（见「输出文件」）。

## chat — 交互模式

    python diag.py chat --config configs/example.yaml

逐条输入十六进制请求，即时显示响应与耗时：

    diag> 22F195
    62F19520533031302E303039  16ms
    diag> 1003
    5003003200C8  15ms
    diag> unlock app
    种子 0147C899BC7BB019C652C23FC8672231 密钥 5D16181468FEDF47...
    6702  15ms
    diag> :q

- 负响应显示 NRC：`7F2231（NRC 0x31）`；无响应显示 `超时无响应`。
- `unlock <app|boot|develop>` 执行安全解锁，失败不断线可继续输入。
- `:q` / `:quit` / `exit` 退出；单条请求失败（如等流控帧超时）不结束会话。

## probe — 单发一条

    python diag.py probe --config configs/example.yaml 22F195
    响应：62F19520533031302E303039

## unlock — 安全解锁

    python diag.py unlock --config configs/example.yaml app

三个安全等级（与 ECU `dia_cfg` b81vs/n60 变体算法一致）：

- `app` — 27 01 取种子、27 02 发 32 字节密钥（DF || Token，AES-CMAC）
- `boot` — 27 11 / 27 12，算法同 app（独立 key factor）
- `develop` — 27 61（携带 16 字节魔数）/ 27 62 发 16 字节密钥

输出示例：

    种子：0147C899BC7BB019C652C23FC8672231
    密钥：5D16181468FEDF472FC337B1ADE353DEA30B886A0BAF0304001E1097E0E0D344
    响应：6702

**会话前置**：默认会话下 ECU 拒绝 0x27（NRC 0x7F），须先进扩展会话；
`boot` 级在应用（扩展）会话同样被拒（NRC 0x7E），需 boot/编程上下文。
单条命令不自动切会话——用 chat 先发 `1003` 再 `unlock`，或流程里在
解锁步骤前加进扩展会话步骤。

退出码与 `probe` 一致：0 成功、1 超时无响应、2 负响应（含 NRC 0x35
密钥错误）。

## 输出文件

每次运行生成 `logs/<名称>_<时间戳>/`：

- `run.log`  人读文本流水（步骤、判定、NRC、0x78 事件、解锁种子/密钥）
- `result.csv`  UDS 级结果表（seq/步骤/请求/响应/NRC/判定/耗时/时间戳）
- `trace.blf`  总线原始帧，CANoe 可直接回放

## 退出码约定

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | run：有 FAIL/TIMEOUT；probe/unlock：ECU 超时无响应 |
| 2 | 配置错误；probe/unlock：收到负响应 |
| 3 | 运行期异常（驱动/传输失败等） |
| 130 | Ctrl+C 中断（已落盘记录不丢） |

## 常见问题

- **`打开 USBCANFD-200U#0 失败`** — 检查 USB 连接与设备索引；确认没有
  其他程序（zcanpro 等）占用设备。
- **`CAN FD 发送失败`** — 多为物理层问题：ECU 掉电、CAN 线未接。ECU
  上电有启动时间，重试前等几秒。
- **`错误：配置错误 ...`** — YAML 有未知字段或缺字段；错误信息会指明
  位置（如 `steps[2].request: 缺失`）。
- **取种子回 NRC 0x7F** — 默认会话不支持 0x27，先进扩展会话（1003）。
- **boot 级回 NRC 0x7E** — 应用会话不支持 boot 解锁，属预期行为。
- **SF 帧要求**：该 ECU 只接受恰好 8 字节的 SF 帧，工具已自动补齐，
  无需关心；写其他测试桩模拟此 ECU 时需注意。

## 测试

    python -m pytest tests/ -v
