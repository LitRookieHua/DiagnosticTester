# 诊断测试工具（diag）

周立功 USBCANFD-200U 上的 UDS 诊断测试工具。经典 CAN / CAN FD 均支持，
按 YAML 测试用例收发诊断数据，文本/CSV/BLF 三路落盘，支持 0x27 安全解锁。

## 快速上手

    python -m pip install -r requirements.txt
    python diag.py run testcase/example.yaml --config configs/example.yaml

前提：ZLGCAN SDK 已按 `vendor/zlgcan/README.md` 组装、CAN 卡已插、
目标 ECU 已上电。免硬件体验可先跑
`python diag.py run testcase/example_mock.yaml --config configs/example_mock.yaml`。

## 目录结构

    configs/                    # 项目配置（每个项目一份，<客户>_<项目名>）
      BAIC_N60/n60.yaml        # can / diagnostic / timing / security
      CMC_RX/rx.yaml
      Chery_T1LFL1/t1lfl1.yaml
      Chery_V2XEU/v2xeu.yaml
      example.yaml / example_mock.yaml
    testcase/                  # 测试用例（每个流程一份，目录与 configs 对应）
      BAIC_N60/verify_1363_final.yaml   # steps / on_fail / 可选 timing 覆盖
      CMC_RX/verify_f4cmc59.yaml
      Chery_T1LFL1/verify_2cf5_clear_2a1e.yaml
      example.yaml / example_mock.yaml
      ...
    configs/<项目>/dtc_map.yaml    # 可选：DTC → 名称（dem 子命令用）
    event_map.yaml / fimfid_map.yaml / dem_dids.yaml   # 同左，均可选
    logs/                      # 每次运行的 run.log / result.csv / trace.blf

同一测试用例换 `--config` 即可跑不同项目。

## 命令一览

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `run` | 跑测试用例 | `python diag.py run testcase/BAIC_N60/x.yaml --config configs/BAIC_N60/n60.yaml` |
| `chat` | 交互模式，逐条发请求 | `python diag.py chat --config configs/BAIC_N60/n60.yaml` |
| `probe` | 单发一条请求 | `python diag.py probe --config configs/BAIC_N60/n60.yaml 22F195` |
| `unlock` | 0x27 安全解锁 | `python diag.py unlock --config configs/BAIC_N60/n60.yaml app` |
| `dem` | 故障检测报告（DTC/event/fimfid） | `python diag.py dem --config configs/CMC_RX/rx.yaml` |

## 配置文件（configs/）

每个项目一份，共四节：

- **can** — 总线接入：`device`（周立功设备名，`mock` 走内置 Mock 驱动、
  免硬件）、`channel` 通道号、`fd` 是否 CAN FD、`baudrate` 仲裁域波特率；
  `data_baudrate` 为 FD 数据域波特率（可省略，默认 2000000）。
- **diagnostic** — 诊断寻址：`request_id` 请求 ID、`response_id` 响应 ID、
  `functional_id` 功能寻址 ID（可选，功能寻址步骤必需）。三个 ID 均限
  11 位标准帧范围 0x001-0x7FF。
- **timing** — 超时与节奏：`p2_ms` 单响应超时（默认 50）、`p2star_ms`
  收到 NRC 0x78 后的等待窗口（默认 5000）、`step_delay_ms` 步骤间默认
  间隔（默认 100）。三项均可省略。
- **security** — 0x27 安全解锁等级（见下）。可选。

## 测试用例（testcase/）

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

**develop（61/62，公司内部通用）内置**：所有项目自动可用，无需配置；
配置里写 `security.develop` 可覆盖。新客户算法 = 在 security.py 注册表
新增一个函数，参数仍走配置。

## run — 跑测试用例

    python diag.py run testcase/BAIC_N60/x.yaml --config configs/BAIC_N60/n60.yaml

输出汇总与退出码：

    PASS 3 / FAIL 0 / TIMEOUT 0 / 共 3/3     # 退出码 0
    PASS 1 / FAIL 1 / TIMEOUT 0 / 共 2/3     # 有 FAIL/TIMEOUT，退出码 1

结果同时落盘到 `logs/<项目>_<用例名>_<时间戳>/`（见「输出文件」）。

## chat — 交互模式

    python diag.py chat --config configs/BAIC_N60/n60.yaml

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

    python diag.py probe --config configs/BAIC_N60/n60.yaml 22F195
    响应：62F19520533031302E303039

## unlock — 安全解锁

    python diag.py unlock --config configs/BAIC_N60/n60.yaml app

等级来自配置 security 节 + 内置 develop（61/62）。

**会话前置**：默认会话下 ECU 拒绝 0x27（NRC 0x7F），须先进扩展会话；
`boot` 级在应用（扩展）会话同样被拒（NRC 0x7E），需 boot/编程上下文。
单条命令不自动切会话——用 chat 先发 `1003` 再 `unlock`，或流程里在
解锁步骤前加进扩展会话步骤。

退出码与 `probe` 一致：0 成功、1 超时无响应、2 负响应或未知等级
（含 NRC 0x35 密钥错误）。

## 输出文件

每次运行生成 `logs/<项目>_<前缀>_<时间戳>/`（前缀：run 为用例名，
chat/probe/unlock 为命令名；项目名取配置的子目录，如
`configs/BAIC_N60/n60.yaml` → BAIC_N60，configs 根下的配置取文件名）：

- `run.log`  人读文本流水（步骤、判定、NRC、0x78 事件、解锁种子/密钥）
- `result.csv`  UDS 级结果表（seq/步骤/请求/响应/NRC/判定/耗时/时间戳）
- `trace.blf`  总线原始帧，CANoe 可直接回放

## 退出码约定

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | run：有 FAIL/TIMEOUT；probe/unlock：ECU 超时无响应 |
| 2 | 配置错误（含未知安全等级）；probe/unlock：收到负响应 |
| 3 | 运行期异常（驱动/传输失败等） |
| 130 | Ctrl+C 中断（已落盘记录不丢） |

## 常见问题

- **`打开 USBCANFD-200U#0 失败`** — 检查 USB 连接与设备索引；确认没有
  其他程序（zcanpro 等）占用设备。
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
