# zlgcan SDK 放置目录

从周立功官网（https://manual.zlg.cn/web/#/55?page_id=2286）下载 ZLGCAN SDK（Windows），
按以下方式组装本目录（Python 必须 64 位，dll 位数须与之匹配）：

| 文件 | 来源 |
| --- | --- |
| `zlgcan.py` | SDK python 包（如 `zlgcan_python_*/zlgcan.py`） |
| `zlgcan.dll` | C SDK 的 **x64** 版本（如 `zlgcan*/zlgcan_x64/zlgcan.dll`） |
| `kerneldlls/` | 与 zlgcan.dll 同目录的 `kerneldlls/`（x64） |

注意：
- python 包里自带的 `zlgcan.dll` 通常是 **32 位**（与 readme 描述相反），64 位 Python
  加载会报 WinError 193，务必用 C SDK 包的 x64 dll。
- 本目录的 `zlgcan.py` 相对官方原版打了补丁：dll 改为按 `__file__` 所在目录加载
  （原版 `"./zlgcan.dll"` 依赖进程 CWD）。
- 设备后端 dll 由 zlgcan.dll 在运行时从自身目录旁的 `kerneldlls/` 加载。

纯软件任务（Task 3-9）不依赖本目录；Task 11 真机验证前必须完成。
