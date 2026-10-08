"""vendor/cxcanfd 绑定冒烟：DLL 能加载、常量/结构体齐全（不碰硬件）。"""


def test_import_and_constants():
    from can_driver import import_cxcanfd
    cx = import_cxcanfd()
    assert cx.VCI_USBCAN2 == 41
    assert cx.TYPE_CAN == 0
    assert cx.TYPE_CANFD == 1
    assert cx.ZCAN() is not None


def test_struct_shapes():
    from can_driver import import_cxcanfd
    cx = import_cxcanfd()
    cfg = cx.ZCAN_CHANNEL_INIT_CONFIG()
    cfg.can_type = cx.TYPE_CANFD
    cfg.config.canfd.mode = 0
    assert cfg.can_type == 1
    msgs = (cx.ZCAN_ReceiveFD_Data * 2)()
    msgs[0].frame.can_id = 0x78C
    msgs[0].frame.len = 8
    msgs[0].frame.data[0] = 0x62
    assert msgs[0].frame.data[0] == 0x62
    assert len(msgs[0].frame.data) == 64
