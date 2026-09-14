# 诊断测试工具（tools/diag）

周立功 USBCANFD-200U 上的 UDS 诊断测试工具。经典 CAN / CAN FD 均支持，
按 YAML 流程收发诊断数据，文本/CSV/BLF 三路落盘。

## 准备

    python -m pip install -r requirements.txt

真机使用前，从周立功官网下载 ZLGCAN SDK，把 python 库目录的 `zlgcan.py`
和 dll 复制到 `vendor/zlgcan/`（详见该目录 README）。

## 用法

    python diag.py run configs/example.yaml              # 跑配置流程
    python diag.py chat --config configs/example.yaml    # 交互模式
    python diag.py probe --config configs/example.yaml 22F195   # 单发一条

## 输出

每次运行生成 `logs/<名称>_<时间戳>/`：

- `run.log`  人读文本流水（步骤、判定、NRC、0x78 事件）
- `result.csv`  UDS 级结果表（Excel/脚本分析）
- `trace.blf`  总线原始帧，CANoe 可直接回放

## 配置

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
- **steps** — 步骤列表：每步必填 `name` 与 `request`（引号包裹的十六进制
  字符串，如 `"22F195"`）；可选 `expect`（期望响应前缀，省略则只发不判、
  超时不算失败）、`timeout_ms`（覆盖该步的 P2 超时，不约束 0x78 后的
  P2* 窗口）、`delay_after_ms`（该步收尾延时，整体覆盖 `step_delay_ms`
  而非叠加）、`addressing`（`physical` 默认 / `functional`：发完即记、
  不支持 `expect`，请求长度不得超过单帧上限——经典 7 字节、FD 62 字节）。
- **on_fail** — 失败策略：`abort`（默认，首个 FAIL/TIMEOUT 即中止）或
  `continue`（跑完全部步骤）。

未知字段（含各嵌套节内拼错的键）在加载期直接报错，不会静默回落默认值。
配置项完整语义见 `configs/` 内示例与本 README。

## 测试

    python -m pytest tests/ -v
