"""Local USB-screen observer and a localhost dashboard. Does not control the phone."""
import argparse
import copy
import json
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
from engine import AnalysisCancelled, BUDGETS, DEFAULT_BUDGET, Engine, RULES
from vision import Grid, detect_grid, read_board, read_marks, validate_position

ROOT = Path(__file__).resolve().parent
MESSAGES = {
    "waiting_capture": "等待 iPhone USB 屏幕",
    "capture_stopped": "取屏已停止，建议已清空",
    "stale": "画面已过期，建议已清空",
    "no_grid": "未找到完整棋盘，请保持棋盘页面显示",
    "uncertain": "部分格子无法确认，等待清晰画面",
    "invalid_count": "黑白棋子数量不一致，暂不计算",
    "black_overline": "黑棋出现长连，请检查规则或识别结果",
    "finished": "棋盘已有五连，对局已结束",
    "draw": "棋盘已满",
    "rule_unknown": "请确认有禁手或无禁手",
    "stabilizing": "等待两帧一致的棋盘",
    "thinking": "正在计算下一手",
    "deepening": "继续深算中，当前为参考建议",
    "ready": "建议已更新",
    "opponent_turn": "等待对方落子",
    "paused": "分析已暂停",
    "error": "分析失败",
}


def atomic_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


class Observer:
    def __init__(self, session, rule=None, image=None):
        self.session = Path(session)
        self.session.mkdir(parents=True, exist_ok=True)
        self.image = Path(image) if image else None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.engine = Engine()
        self.engine_lock = threading.Lock()
        self.settings = {"rule": rule or "freestyle", "size": 15, "budget_ms": DEFAULT_BUDGET, "my_color": 2, "paused": False}
        try:
            saved = json.loads((self.session / "settings.json").read_text())
            for key, allowed in (("budget_ms", BUDGETS), ("size", (15, 19)),
                                 ("my_color", (0, 1, 2)), ("rule", (*RULES, "unknown"))):
                if saved.get(key) in allowed:
                    self.settings[key] = saved[key]
        except (OSError, ValueError, AttributeError):
            pass
        if rule is not None:
            self.settings["rule"] = rule
        self.grid = None
        self.manual_grid = None
        self.frame = b""
        self.state = {"status": "waiting_capture", "source": "offline_image" if image else "live_usb_screen",
                      "board": None, "suggestion": None, "captured_at": None, "frame_id": None}
        self.generation = 0
        self.board_key = None
        self.seen_key = None
        self.stable_count = 0
        self.last_frame_token = None
        self.inflight = None
        self.search_cancel = None
        self.detect_at = 0

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            state["settings"] = dict(self.settings)
            state["grid"] = self.grid.to_dict() if self.grid else None
            captured_at = state.get("captured_at")
            state["frame_age_ms"] = max(0, round((time.time()-captured_at)*1000)) if captured_at else None
            if not self.image and captured_at and time.time()-captured_at > 2:
                state.update(status="stale", suggestion=None, board=None)
            state["message"] = state.get("error") or MESSAGES.get(state["status"], state["status"])
            return state

    def invalidate(self, status, error=None, clear_board=True):
        with self.lock:
            if self.search_cancel is not None:
                self.search_cancel.set()
            if self.state["status"] != status or self.state.get("suggestion"):
                self.generation += 1
            self.state.update(status=status, suggestion=None, error=error)
            if clear_board:
                self.state["board"] = None
                self.state["position"] = None
                self.state["last_moves"] = None
            self.board_key = None
            self.seen_key = None
            self.stable_count = 0

    def configure(self, values):
        with self.lock:
            updates = dict(self.settings)
            if "rule" in values:
                if values["rule"] not in (*RULES, "unknown"):
                    raise ValueError("无效规则")
                updates["rule"] = values["rule"]
            for key, allowed in (("size", (15, 19)), ("budget_ms", BUDGETS),
                                 ("my_color", (0, 1, 2))):
                if key in values:
                    value = int(values[key])
                    if value not in allowed:
                        raise ValueError(f"无效设置：{key}")
                    updates[key] = value
            if "paused" in values:
                if not isinstance(values["paused"], bool):
                    raise ValueError("paused 必须为布尔值")
                updates["paused"] = values["paused"]
            if updates["size"] != self.settings["size"]:
                self.grid = self.manual_grid = None
            self.settings = updates
            atomic_json(self.session / "settings.json", updates)
            self.generation += 1
            self.invalidate("paused" if updates["paused"] else "stabilizing")
            self.last_frame_token = None
            return self.snapshot()

    def calibrate(self, values):
        with self.lock:
            w, h = self.state.get("width"), self.state.get("height")
            if not w or not h or not self.frame:
                raise ValueError("还没有手机画面")
            grid = Grid(*(float(values[key]) for key in ("x0", "y0", "x1", "y1")),
                        self.settings["size"], w, h, "manual")
            if not grid.valid():
                raise ValueError("请选择棋盘左上和右下的交叉点")
            im = cv2.imdecode(np.frombuffer(self.frame, np.uint8), cv2.IMREAD_COLOR)
            read_board(im, grid)  # Require actual grid lines at the selected positions.
            self.grid = self.manual_grid = grid
            self.generation += 1
            self.invalidate("stabilizing")
            self.last_frame_token = None
            return self.snapshot()

    def reset_grid(self):
        with self.lock:
            self.grid = self.manual_grid = None
            self.detect_at = 0
            self.generation += 1
            self.invalidate("no_grid")
            self.last_frame_token = None
            return self.snapshot()

    def _load(self):
        if self.image:
            data = self.image.read_bytes()
            token = (self.image.stat().st_mtime_ns, self.generation)
            return data, {"captured_at": None, "frame_id": str(token), "status": "offline"}, token
        try:
            first = json.loads((self.session / "capture.json").read_text())
            if first.get("status") != "capturing":
                self.invalidate("waiting_capture")
                return None
            date = first.get("captured_at", 0)
            if time.time() - date > 2:
                self.invalidate("stale")
                return None
            token = (first["sequence"], date)
            if token == self.last_frame_token:
                return None
            data = (self.session / "latest.jpg").read_bytes()
            after = json.loads((self.session / "capture.json").read_text())
            if after != first:
                return None  # Never pair an old timestamp with a newly replaced image.
            first["frame_id"] = f"{date:.6f}:{first['sequence']}"
            return data, first, token
        except (OSError, ValueError, KeyError):
            self.invalidate("waiting_capture")
            return None

    def observe(self, image, metadata, raw=b""):
        started = time.perf_counter()
        h, w = image.shape[:2]
        with self.lock:
            self.frame = raw
            self.state.update({k: metadata.get(k) for k in ("captured_at", "frame_id")})
            self.state.update(width=w, height=h)
            settings = dict(self.settings)
            if self.grid and (self.grid.width != w or self.grid.height != h):
                self.grid = self.manual_grid = None
                self.invalidate("no_grid")
            if settings["paused"]:
                self.invalidate("paused")
                return
            grid = self.grid
            if grid is None and time.monotonic() >= self.detect_at:
                self.detect_at = time.monotonic() + 0.7
                grid = detect_grid(image, settings["size"])
                self.grid = grid
            if grid is None:
                self.invalidate("no_grid")
                return
            try:
                board, uncertain = read_board(image, grid)
            except ValueError:
                if self.manual_grid is None:
                    self.grid = None
                self.invalidate("no_grid")
                return
            matrix = board.tolist()
            position = validate_position(board, settings["rule"])
            self.state.update(board=matrix, position=position, uncertain=uncertain,
                              last_moves=read_marks(image, grid, board),
                              recognition_ms=round((time.perf_counter()-started)*1000, 1))
            if position["status"] != "valid":
                self.invalidate(position["status"], clear_board=False)
                return
            if settings["rule"] == "unknown":
                self.invalidate("rule_unknown", clear_board=False)
                return
            if settings["my_color"] and position["turn"] != settings["my_color"]:
                self.invalidate("opponent_turn", clear_board=False)
                return
            key = (board.tobytes(), settings["rule"], settings["budget_ms"], settings["my_color"])
            if key != self.seen_key:
                if self.search_cancel is not None:
                    self.search_cancel.set()
                self.seen_key = key
                self.stable_count = 1
                self.generation += 1
                self.board_key = None
                self.state.update(status="stabilizing", suggestion=None, error=None)
                self.state["change_detected_at"] = time.time()
                return
            self.stable_count += 1
            if key == self.board_key:
                return
            if self.inflight is not None:
                self.state.update(status="thinking", suggestion=None)
                return
            self.board_key = key
            self.state.update(status="thinking", suggestion=None, error=None)
            generation = self.generation
            self.inflight = generation
            cancel = self.search_cancel = threading.Event()
        threading.Thread(target=self._analyse, args=(matrix, settings, generation, cancel), daemon=True).start()

    def _publish(self, suggestion, generation, final):
        with self.lock:
            if generation != self.generation or self.stop.is_set():
                return
            captured_at = self.state.get("captured_at")
            if not self.image and (not captured_at or time.time()-captured_at > 2):
                self.invalidate("stale")
                return
            suggestion["update_ms"] = round((time.time()-self.state["change_detected_at"])*1000, 1)
            suggestion["computed_at"] = time.time()
            suggestion["generation"] = generation
            self.state.update(status="ready" if final else "deepening", suggestion=suggestion, error=None)
            atomic_json(self.session / "latest-state.json", self.snapshot())

    def _analyse(self, matrix, settings, generation, cancel):
        try:
            suggestion = self.engine.analyse(matrix, settings["rule"], settings["budget_ms"],
                progress=lambda suggestion: self._publish(suggestion, generation, False), cancel=cancel)
            self._publish(suggestion, generation, True)
            with self.lock:
                if generation != self.generation or self.stop.is_set() or cancel.is_set():
                    return
                if self.state["status"] != "ready":
                    return
                with (self.session / "latency.jsonl").open("a") as handle:
                    handle.write(json.dumps({"source": self.state["source"], "captured_at": self.state.get("captured_at"),
                        "black": self.state["position"]["black"], "white": self.state["position"]["white"],
                        "recognition_ms": self.state["recognition_ms"], **suggestion}) + "\n")
        except AnalysisCancelled:
            pass
        except Exception as error:
            with self.lock:
                with (self.session / "analysis-errors.jsonl").open("a") as handle:
                    handle.write(json.dumps({"time": time.time(), "generation": generation,
                        "error": str(error), "board": matrix}, ensure_ascii=False) + "\n")
                if generation == self.generation:
                    self.state.update(status="error", error=str(error), suggestion=None)
        finally:
            with self.lock:
                self.inflight = None
                if self.search_cancel is cancel:
                    self.search_cancel = None

    def run(self):
        cv2.setNumThreads(1)
        last_written = None
        while not self.stop.is_set():
            try:
                loaded = self._load()
                if loaded:
                    data, metadata, token = loaded
                    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    if image is None:
                        self.invalidate("error", "无法解码取屏画面")
                    else:
                        self.last_frame_token = token
                        self.observe(image, metadata, data)
                elif self.image and self.last_frame_token:
                    # An offline still is explicitly labelled; a second read permits validation.
                    data = self.image.read_bytes()
                    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    self.observe(image, {"captured_at": None, "frame_id": "offline"}, data)
            except Exception as error:
                self.invalidate("error", str(error))
            with self.lock:
                current = self.snapshot()
                key = (current["status"], current.get("frame_id"), self.generation, current.get("error"))
                if key != last_written:
                    atomic_json(self.session / "latest-state.json", current)
                    last_written = key
            self.stop.wait(0.04 if not self.image else 0.2)
        if self.search_cancel is not None:
            self.search_cancel.set()
        self.engine.close()
        self.invalidate("capture_stopped")
        atomic_json(self.session / "latest-state.json", self.snapshot())


def serve(observer, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed(self):
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}") and (
                origin is None or origin in (f"http://127.0.0.1:{self.server.server_port}",
                                            f"http://localhost:{self.server.server_port}"))

        def send(self, status, data, mime="application/json"):
            if isinstance(data, dict):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed():
                self.send(403, {"error": "不允许跨站访问"})
                return
            path = urlparse(self.path).path
            if path == "/":
                self.send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self.send(200, observer.snapshot())
            elif path == "/frame.jpg":
                with observer.lock:
                    data = observer.frame
                self.send(200 if data else 404, data, "image/jpeg")
            else:
                self.send(404, {"error": "未找到"})

        def do_POST(self):
            if not self.allowed():
                self.send(403, {"error": "不允许跨站访问"})
                return
            try:
                count = int(self.headers.get("Content-Length", "0"))
                if not 0 < count <= 16384 or not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("请求格式错误")
                values = json.loads(self.rfile.read(count))
                if not isinstance(values, dict):
                    raise ValueError("请求格式错误")
                path = urlparse(self.path).path
                if path == "/api/settings":
                    state = observer.configure(values)
                elif path == "/api/calibrate":
                    state = observer.calibrate(values)
                elif path == "/api/reset-grid":
                    state = observer.reset_grid()
                elif path == "/api/stop":
                    observer.stop.set()
                    observer.invalidate("capture_stopped")
                    state = observer.snapshot()
                else:
                    self.send(404, {"error": "未找到"})
                    return
                self.send(200, state)
            except (ValueError, TypeError, KeyError) as error:
                self.send(400, {"error": str(error)})
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--session", default=str(ROOT / ".runtime"))
    parser.add_argument("--rule", choices=[*RULES, "unknown"])
    parser.add_argument("--image")
    parser.add_argument("--device")
    parser.add_argument("--my-color", type=int, choices=(0, 1, 2))
    parser.add_argument("--duration", type=float, default=86400)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    observer = Observer(args.session, args.rule, args.image)
    if args.my_color is not None:
        observer.settings["my_color"] = args.my_color
    server = serve(observer, args.port)
    capture = None
    capture_log = None
    if not args.image:
        capture_log = (observer.session / "capture.log").open("a")
        cmd = [str(ROOT / "bin/phone-capture"), "--output", str(observer.session),
               "--duration", str(args.duration)]
        if args.device:
            cmd += ["--device", args.device]
        capture = subprocess.Popen(cmd, stdout=capture_log, stderr=subprocess.STDOUT)
    worker = threading.Thread(target=observer.run, daemon=True)
    worker.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "source": observer.state["source"],
                      "backend_pid": os.getpid(), "capture_pid": capture.pid if capture else None}), flush=True)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: observer.stop.set())
    try:
        deadline = time.monotonic() + args.duration
        while not observer.stop.wait(min(1, max(0, deadline-time.monotonic()))):
            if time.monotonic() >= deadline or (args.parent_pid and os.getppid() != args.parent_pid):
                break
    finally:
        observer.stop.set()
        if capture:
            capture.terminate()
            try:
                capture.wait(timeout=8)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait()
        worker.join(timeout=8)
        server.shutdown()
        server.server_close()
        if capture_log:
            capture_log.close()


if __name__ == "__main__":
    main()
