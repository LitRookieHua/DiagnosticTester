# diag 工具适配创芯 CANFD 卡：新增 CxCanFdDriver

日期：2026-10-08
状态：已确认

## 背景与目标

diag 工具目前只支持周立功 USBCANFD 系列卡（`ZlgCanDriver` → `vendor/zlgcan/`）。
团队同时持有创芯科技双通道 USB-CANFD 卡（示例 SDK：`spec/python(x64)_example_python3.8.8_v2.0`，
`ControlCANFD.dll`，设备类型 `VCI_USBCAN2=41`）。目标：一套代码同时支持两种卡，
按 yaml 配置的 `can.device` 名分发，ZLG 支持不受影响。

创芯 API 是 ZLG `ZCAN_*` 函数族的克隆（结构体几乎相同），适配成本低。

## 1. 文件布局与路由

```
diag/
├── diag.py                  make_driver() 三路由
├── can_driver.py            CanFrame、CanDriver 基类、ZlgCanDriver、新增 CxCanFdDriver
├── vendor/
│   ├── zlgcan/              （现有，不动）
│   └── cxcanfd/             （新增）
│       ├── ControlCANFD.dll # 64 位，从 spec 示例目录复制
│       ├── cxcanfd.py       自写 ctypes 绑定
│       └── README.md        来源、Python 位数要求
└── configs/example_cx.yaml  创芯示例配置
```

`make_driver()`（diag.py）路由规则：

| can.device | 驱动 |
| --- | --- |
| `mock` | `MockCanDriver` |
| `USBCANFD-100U` / `USBCANFD-200U` / `USBCANFD-800U` | `ZlgCanDriver` |
| `CX-USBCANFD` | `CxCanFdDriver` |
| 其他 | RuntimeError，报错信息列出全部可选设备名 |

## 2. vendor/cxcanfd/cxcanfd.py 绑定模块

创芯没有官方 Python 包装（示例是裸 ctypes 脚本），自写绑定，接口风格模仿
zlgcan：模块级结构体 + 常量 + `ZCAN` 类。

- **结构体**（从官方示例照搬，字段完全一致）：
  `ZCAN_CHANNEL_INIT_CONFIG`（含 can/canfd union）、`ZCAN_CAN_FRAME`、
  `ZCAN_CANFD_FRAME`、`ZCAN_Transmit_Data`、`ZCAN_Receive_Data`、
  `ZCAN_TransmitFD_Data`、`ZCAN_ReceiveFD_Data`
- **常量**：`VCI_USBCAN2 = 41`、`TYPE_CAN = 0`、`TYPE_CANFD = 1`、`STATUS_OK = 1`
- **ZCAN 类**方法：`OpenDevice`、`CloseDevice`、`SetAbitBaud`、`SetDbitBaud`、
  `SetCANFDStandard`、`InitCAN`、`StartCAN`、`ResetCAN`、`Transmit`、`TransmitFD`、
  `GetReceiveNum`、`Receive`、`ReceiveFD`
- **函数原型**：按示例声明 `argtypes/restype`，其中 `ZCAN_OpenDevice` /
  `ZCAN_InitCAN` 返回 `c_void_p`（句柄），返回值 0 视为失败
- **DLL 加载**：按**模块所在目录**拼 `ControlCANFD.dll` 路径
  （示例用 `'./ControlCANFD.dll'` 依赖 CWD，换目录运行即失败，必须修正）

## 3. CxCanFdDriver 行为（对齐 ZlgCanDriver 惯例）

```python
CxCanFdDriver(device="CX-USBCANFD", device_index=0, channel=0,
              baudrate=500000, data_baudrate=2000000, fd=True)
```

初始化序列（任一步失败即 `CloseDevice` 回滚并抛 RuntimeError）：

```
OpenDevice(VCI_USBCAN2, device_index, 0)
SetAbitBaud(dev, channel, baudrate)
SetDbitBaud(dev, channel, data_baudrate)
SetCANFDStandard(dev, channel, 0)        # 0 = ISO 模式
InitCAN(dev, channel, TYPE_CANFD, mode=0)  # 正常模式，经典+FD 双队列
StartCAN(chn)
启动接收线程
```

| 环节 | 做法 |
| --- | --- |
| 导入 | `import_cxcanfd()`：vendor/cxcanfd 插入 `sys.path` + `add_dll_directory`，同 `import_zlgcan()` |
| 设备名 | 白名单 `{"CX-USBCANFD": VCI_USBCAN2}`，其他名字报错并列出可选项 |
| 接收线程 | 轮询 `GetReceiveNum(TYPE_CANFD)` + `GetReceiveNum(TYPE_CAN)`，各自 `ReceiveFD`/`Receive`，转 `CanFrame` 入队；`fd=False` 时只收经典队列 |
| 发送 FD 帧 | 沿用 ZLG 补齐策略：最小补到 8 字节、更长按 DLC 阶梯补齐（ECU CanIf quirk，与卡无关）；`brs = bitrate_switch`；`eff = 0`（当前 ECU 均为标准帧） |
| 发送经典帧 | 长度 ≤8 校验；帧模式与 `fd` 不匹配时报错（同 ZLG） |
| 关闭 | 停接收线程 → `ResetCAN` → `CloseDevice` |

## 4. 与 ZLG 的 API 差异及处理

| 差异点 | ZLG | 创芯 | 处理 |
| --- | --- | --- | --- |
| 波特率设置 | `ZCAN_SetValue` 字符串键 | `ZCAN_SetAbitBaud` / `ZCAN_SetDbitBaud` 专用函数 | 直接映射现有 `baudrate` / `data_baudrate` 配置 |
| ISO/非ISO | 无显式设置 | `ZCAN_SetCANFDStandard` | 固定 0（ISO） |
| 终端电阻 | 软件开 `initenal_resistance` | 无 API | README 注明由卡上硬件拨码/跳线处理 |
| 发送回显 | `set_device_tx_echo=0` | 无对应设置 | 不设置（正常模式无自发自收） |
| Python 包装 | 官方 zlgcan.py | 无 | 自写 vendor/cxcanfd/cxcanfd.py |

## 5. 配置与文档

- yaml 字段零新增：`device: CX-USBCANFD` + 现有 `channel/fd/baudrate/data_baudrate`
- 新增 `configs/example_cx.yaml`（复制 example.yaml，改 device 字段）
- README 补「创芯卡使用」小节：DLL 位置、终端电阻硬件处理、64 位 Python 要求

## 6. 测试

- **回归**：现有 tests 全部基于 MockCanDriver，跑全量确认无破坏
- **新增 `tests/test_cx_driver.py`**：注入 fake `ZCAN` 类（不依赖真 DLL/硬件）：
  - 初始化调用序列与参数（含失败回滚：StartCAN 失败时 CloseDevice 被调）
  - FD 发送补齐（<8 补 8、非阶梯长度补到阶梯）
  - 经典帧 >8 字节报错、帧模式不匹配报错
  - 接收线程帧结构体 → `CanFrame` 转换（FD 与经典两条路径）
- **真机验证**：插卡后 `python diag.py probe --config configs/example_cx.yaml 22F195`
