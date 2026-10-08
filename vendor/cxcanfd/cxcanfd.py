"""创芯 ControlCANFD.dll 的 ctypes 绑定。

创芯无官方 Python 包装（官方示例为裸 ctypes 脚本），本模块自写，
接口风格对齐 vendor/zlgcan/zlgcan.py：结构体 + 常量 + ZCAN 类。

DLL 按本模块所在目录加载（官方示例 './ControlCANFD.dll' 依赖进程 CWD，
换目录运行会失败）。Python 位数须与 DLL 一致（仓库内置 64 位 DLL）。
"""
from ctypes import (Structure, Union, byref, c_long, c_ubyte, c_uint, c_ulong,
                    c_ulonglong, c_ushort, c_void_p, windll)
from pathlib import Path

VCI_USBCAN2 = 41
STATUS_OK = 1
TYPE_CAN = 0
TYPE_CANFD = 1


class _ZCAN_CHANNEL_CAN_INIT_CONFIG(Structure):
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("reserved", c_uint),
                ("filter", c_ubyte),
                ("timing0", c_ubyte),
                ("timing1", c_ubyte),
                ("mode", c_ubyte)]


class _ZCAN_CHANNEL_CANFD_INIT_CONFIG(Structure):
    _fields_ = [("acc_code", c_uint),
                ("acc_mask", c_uint),
                ("abit_timing", c_uint),
                ("dbit_timing", c_uint),
                ("brp", c_uint),
                ("filter", c_ubyte),
                ("mode", c_ubyte),
                ("pad", c_ushort),
                ("reserved", c_uint)]


class _ZCAN_CHANNEL_INIT_CONFIG(Union):
    _fields_ = [("can", _ZCAN_CHANNEL_CAN_INIT_CONFIG),
                ("canfd", _ZCAN_CHANNEL_CANFD_INIT_CONFIG)]


class ZCAN_CHANNEL_INIT_CONFIG(Structure):
    _fields_ = [("can_type", c_uint),
                ("config", _ZCAN_CHANNEL_INIT_CONFIG)]


class ZCAN_CAN_FRAME(Structure):
    _fields_ = [("can_id", c_uint, 29),
                ("err", c_uint, 1),
                ("rtr", c_uint, 1),
                ("eff", c_uint, 1),
                ("can_dlc", c_ubyte),
                ("__pad", c_ubyte),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data", c_ubyte * 8)]


class ZCAN_CANFD_FRAME(Structure):
    _fields_ = [("can_id", c_uint, 29),
                ("err", c_uint, 1),
                ("rtr", c_uint, 1),
                ("eff", c_uint, 1),
                ("len", c_ubyte),
                ("brs", c_ubyte, 1),
                ("esi", c_ubyte, 1),
                ("__res", c_ubyte, 6),
                ("__res0", c_ubyte),
                ("__res1", c_ubyte),
                ("data", c_ubyte * 64)]


class ZCAN_Transmit_Data(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("transmit_type", c_uint)]


class ZCAN_Receive_Data(Structure):
    _fields_ = [("frame", ZCAN_CAN_FRAME), ("timestamp", c_ulonglong)]


class ZCAN_TransmitFD_Data(Structure):
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("transmit_type", c_uint)]


class ZCAN_ReceiveFD_Data(Structure):
    _fields_ = [("frame", ZCAN_CANFD_FRAME), ("timestamp", c_ulonglong)]


_dll = windll.LoadLibrary(str(Path(__file__).resolve().parent / "ControlCANFD.dll"))

_dll.ZCAN_OpenDevice.restype = c_void_p
_dll.ZCAN_SetAbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
_dll.ZCAN_SetDbitBaud.argtypes = (c_void_p, c_ulong, c_ulong)
_dll.ZCAN_SetCANFDStandard.argtypes = (c_void_p, c_ulong, c_ulong)
_dll.ZCAN_InitCAN.argtypes = (c_void_p, c_ulong, c_void_p)
_dll.ZCAN_InitCAN.restype = c_void_p
_dll.ZCAN_StartCAN.argtypes = (c_void_p,)
_dll.ZCAN_Transmit.argtypes = (c_void_p, c_void_p, c_ulong)
_dll.ZCAN_TransmitFD.argtypes = (c_void_p, c_void_p, c_ulong)
_dll.ZCAN_GetReceiveNum.argtypes = (c_void_p, c_ulong)
_dll.ZCAN_Receive.argtypes = (c_void_p, c_void_p, c_ulong, c_long)
_dll.ZCAN_ReceiveFD.argtypes = (c_void_p, c_void_p, c_ulong, c_long)
_dll.ZCAN_ResetCAN.argtypes = (c_void_p,)
_dll.ZCAN_CloseDevice.argtypes = (c_void_p,)


class ZCAN:
    def OpenDevice(self, device_type, device_index, reserved=0):
        return _dll.ZCAN_OpenDevice(device_type, device_index, reserved)

    def CloseDevice(self, device_handle):
        return _dll.ZCAN_CloseDevice(device_handle)

    def SetAbitBaud(self, device_handle, channel, baud):
        return _dll.ZCAN_SetAbitBaud(device_handle, channel, baud)

    def SetDbitBaud(self, device_handle, channel, baud):
        return _dll.ZCAN_SetDbitBaud(device_handle, channel, baud)

    def SetCANFDStandard(self, device_handle, channel, standard):
        return _dll.ZCAN_SetCANFDStandard(device_handle, channel, standard)

    def InitCAN(self, device_handle, channel_index, config):
        return _dll.ZCAN_InitCAN(device_handle, channel_index, byref(config))

    def StartCAN(self, channel_handle):
        return _dll.ZCAN_StartCAN(channel_handle)

    def ResetCAN(self, channel_handle):
        return _dll.ZCAN_ResetCAN(channel_handle)

    def GetReceiveNum(self, channel_handle, can_type):
        return _dll.ZCAN_GetReceiveNum(channel_handle, can_type)

    def Transmit(self, channel_handle, msgs, length):
        return _dll.ZCAN_Transmit(channel_handle, byref(msgs), length)

    def TransmitFD(self, channel_handle, msgs, length):
        return _dll.ZCAN_TransmitFD(channel_handle, byref(msgs), length)

    def Receive(self, channel_handle, msgs, length, timeout):
        return _dll.ZCAN_Receive(channel_handle, byref(msgs), length, timeout)

    def ReceiveFD(self, channel_handle, msgs, length, timeout):
        return _dll.ZCAN_ReceiveFD(channel_handle, byref(msgs), length, timeout)
