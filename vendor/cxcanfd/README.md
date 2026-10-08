# 创芯 ControlCANFD SDK 放置目录

来源：创芯科技官方 python 示例包 `python(x64)_example_python3.8.8_v2.0`
（随 CAN 卡提供，无公开下载渠道）。

与 vendor/zlgcan 不同，本目录 DLL **随仓库内置**：创芯 SDK 无公开下载
门户，克隆即用、无需手动组装。

| 文件 | 说明 |
| --- | --- |
| `ControlCANFD.dll` | 64 位 DLL（Python 须为 64 位；32 位 Python 需向厂家索取 x86 版） |
| `cxcanfd.py` | 自写 ctypes 绑定（官方无 Python 包装），DLL 按 `__file__` 所在目录加载 |

设备类型：`VCI_USBCAN2 = 41`（双通道 USB-CANFD，配置里设备名 `CX-USBCANFD`）。

注意：终端电阻无软件开关（ZLG 是软件开启 `initenal_resistance`），
需按卡上拨码/跳线或外接电阻手动处理。
