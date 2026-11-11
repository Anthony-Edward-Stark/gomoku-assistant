"""Ephemeral frames from a browser-selected window on the loopback dashboard."""
from secrets import token_urlsafe
import time

MAX_FRAME_BYTES = 2 * 1024 * 1024
STALE_SECONDS = 2
WINDOW_SOURCE = "browser_window"
LIVE_SOURCES = {"live_usb_screen", WINDOW_SOURCE}


class BrowserCapture:
    """Access under the observer's lock; old tabs cannot publish into a new lease."""

    def __init__(self):
        self.token = None
        self.serial = 0
        self.frame = None

    def owns(self, token):
        return self.token is not None and token == self.token

    def begin(self):
        self.token = token_urlsafe(24)
        self.frame = None
        return self.token

    def accept(self, token, frame):
        if not self.owns(token):
            return False
        self.serial += 1
        self.frame = (frame, {"frame_id": f"window:{self.serial}", "captured_at": time.time(),
                              "capture_token": token}, self.serial, time.monotonic())
        return True

    def finish(self, token):
        if not self.owns(token):
            return False
        self.token = self.frame = None
        return True

    def load(self, last_serial):
        if self.frame:
            frame, metadata, serial, arrived = self.frame
            if time.monotonic() - arrived > STALE_SECONDS:
                self.frame = None
                return None, "stale"
            if serial != last_serial:
                return (frame, metadata, serial), None
        return None, None


def handle_capture(handler, observer, path):
    if path not in ("/api/capture/start", "/api/capture/frame", "/api/capture/end"):
        return False
    source = observer.browser_capture
    if source is None:
        handler.send(404, {"error": "当前未启用窗口取屏"})
        return True
    if handler.headers.get("Origin") != "http://" + handler.headers.get("Host", ""):
        handler.send(403, {"error": "不允许跨站访问"})
        return True
    token = handler.headers.get("X-Capture-Token")
    if path == "/api/capture/frame":
        try:
            count = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            count = 0
        if not 0 < count <= MAX_FRAME_BYTES or handler.headers.get("Content-Type") != "image/jpeg":
            handler.send(400, {"error": "画面必须为 JPEG，且不超过 2 MiB"})
            return True
        frame = handler.rfile.read(count)
        if len(frame) != count or not frame.startswith(b"\xff\xd8\xff") or not frame.endswith(b"\xff\xd9"):
            handler.send(400, {"error": "JPEG 画面不完整"})
            return True
        with observer.lock:
            accepted = source.accept(token, frame)
        handler.send(200 if accepted else 409, {"accepted": accepted})
        return True
    if handler.headers.get("Content-Type") != "application/json" or handler.headers.get("Content-Length") != "2":
        handler.send(400, {"error": "请求格式错误"})
        return True
    if handler.rfile.read(2) != b"{}":
        handler.send(400, {"error": "请求格式错误"})
        return True
    with observer.lock:
        if path == "/api/capture/start":
            token = source.begin()
            observer.reset_window_input("waiting_window", new_window=True)
            result = {"token": token}
        else:
            if source.finish(token):
                observer.reset_window_input("waiting_window")
            result = {"stopped": True}
    handler.send(200, result)
    return True
