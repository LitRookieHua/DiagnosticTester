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

见 `configs/example.yaml` 注释与设计文档
`docs/superpowers/specs/2026-09-14-diag-test-tool-design.md`。

## 测试

    python -m pytest tests/ -v
