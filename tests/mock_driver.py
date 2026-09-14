from can_driver import CanDriver, CanFrame


class MockCanDriver(CanDriver):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.on_send = None

    def send(self, frame):
        self.sent.append(frame)
        if self.on_send:
            self.on_send(frame)

    def push_rx(self, can_id, data, is_fd=False):
        self.rx_queue.put(CanFrame(can_id, bytes(data), is_fd=is_fd))
