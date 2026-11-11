"""Persistent Rapfi process, with a bounded Gomocup search and checked coordinates."""
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

RULES = {"freestyle": 0, "standard": 1, "renju": 4}
BUDGETS = (250, 500, 1000, 2000, 5000, 10000, 20000, 30000)
DEFAULT_BUDGET = 2000
DEFAULT_THREADS = max(1, min(4, os.cpu_count() or 2))
DEFAULT_MEMORY_MB = 512


class AnalysisCancelled(Exception):
    """A changed position interrupted the search; the process remains reusable."""


class Engine:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get("ASSISTANT_ENGINE_DIR") or Path(__file__).parent / "engine").resolve()
        self.process = None
        self.lines = queue.Queue()
        self.lock = threading.Lock()
        self.ready = False

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        self.lines = queue.Queue()
        executable = "pbrain-rapfi.exe" if os.name == "nt" else "pbrain-rapfi"
        self.process = subprocess.Popen([str(self.directory / executable)],
            cwd=self.directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process, lines = self.process, self.lines

        def receive():
            for line in process.stdout:
                lines.put(line.strip())
            lines.put(None)
        threading.Thread(target=receive, daemon=True).start()
        self.ready = False

    def _send(self, text):
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("Rapfi 已退出")
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def _until(self, predicate, timeout, on_line=None, cancel=None):
        deadline = time.monotonic() + timeout
        seen = []
        cancelled = False
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set() and not cancelled:
                self._send("STOP")
                cancelled = True
                deadline = min(deadline, time.monotonic() + 2)
            try:
                line = self.lines.get(timeout=min(0.05, max(0.001, deadline-time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("Rapfi 已退出：" + "; ".join(seen[-4:]))
            if line.startswith("ERROR"):
                raise RuntimeError(line)
            seen.append(line)
            if on_line is not None and not cancelled:
                on_line(line)
            if predicate(line):
                if cancelled or (cancel is not None and cancel.is_set()):
                    raise AnalysisCancelled()
                return line, seen
        raise TimeoutError("Rapfi 响应超时：" + "; ".join(seen[-4:]))

    def analyse(self, board, rule, budget_ms=DEFAULT_BUDGET, *, progress=None, cancel=None,
                threads=DEFAULT_THREADS, memory_mb=DEFAULT_MEMORY_MB):
        if rule not in RULES:
            raise ValueError("请先确认规则")
        size = len(board)
        if size not in (15, 19) or any(len(row) != size or any(cell not in (0, 1, 2) for cell in row) for row in board):
            raise ValueError("棋盘必须完整，且格子只能为空、黑棋或白棋")
        if size != 15 and rule != "freestyle":
            raise ValueError("当前禁手和恰好五连权重仅支持 15 路棋盘")
        black = [(x, y) for y in range(size) for x in range(size) if board[y][x] == 1]
        white = [(x, y) for y in range(size) for x in range(size) if board[y][x] == 2]
        if len(black) not in (len(white), len(white)+1):
            raise ValueError("黑白棋子数量不符合轮流落子的规则")
        if budget_ms not in BUDGETS or not 1 <= threads <= 8 or not 32 <= memory_mb <= 1024:
            raise ValueError("无效的引擎计算预算")
        turn = 1 if len(black) == len(white) else 2
        # Piskvork side 1 is SELF, not necessarily BLACK. Place BLACK first.
        entries = []
        for i in range(len(black)):
            x, y = black[i]
            entries.append(f"{x},{y},{1 if turn == 1 else 2}")
            if i < len(white):
                x, y = white[i]
                entries.append(f"{x},{y},{1 if turn == 2 else 2}")
        with self.lock:
            if cancel is not None and cancel.is_set():
                raise AnalysisCancelled()
            start = time.monotonic()
            self._start()
            try:
                self._send(f"START {size}")
                _, startup = self._until(lambda line: line == "OK", 8)
                self.ready = True
                if cancel is not None and cancel.is_set():
                    raise AnalysisCancelled()
                commands = [f"INFO rule {RULES[rule]}", f"INFO timeout_turn {int(budget_ms)}",
                    "INFO timeout_match 100000000", "INFO time_left 100000000",
                    f"INFO thread_num {threads}", f"INFO max_memory {memory_mb*1024*1024}",
                    "INFO pondering 0", "INFO strength 100", "INFO show_detail 3",
                    "BOARD", *entries, "DONE"]
                info = {}
                pv_index = 0
                published_at = 0

                def result(move, final=False):
                    x, y = move
                    if not (0 <= x < size and 0 <= y < size and board[y][x] == 0):
                        raise RuntimeError("Rapfi 返回了无效或已占用的落点")
                    pv = [[int(a), int(b)] for a, b in re.findall(r"(\d+),(\d+)", info.get("BESTLINE", ""))]
                    matches_pv = bool(pv) and pv[0] == [x, y]
                    return {"move": [x, y], "turn": turn, "rule": rule,
                            "coordinate": f"{chr(65+x)}{size-y}", "pv": pv[:10] if matches_pv else [],
                            "depth": int(info["DEPTH"]) if "DEPTH" in info and matches_pv else None,
                            "evaluation": int(info["EVAL"]) if matches_pv and re.fullmatch(r"[+-]?\d+", info.get("EVAL", "")) else None,
                            "evaluation_text": info.get("EVAL") if matches_pv else None,
                            "engine_ms": round((time.monotonic()-start)*1000, 1),
                            "search_ms": int(info["TOTALTIME"]) if "TOTALTIME" in info else None,
                            "nodes": int(info["TOTALNODES"]) if "TOTALNODES" in info else None,
                            "budget_ms": budget_ms, "threads": threads, "memory_mb": memory_mb,
                            "final": final}

                def receive_info(line):
                    nonlocal pv_index, published_at
                    match = re.fullmatch(r"INFO (PV|DEPTH|EVAL|TOTALTIME|TOTALNODES|BESTLINE) (.+)", line)
                    if not match:
                        return
                    key, value = match.groups()
                    if key == "PV":
                        if value == "DONE":
                            pv = re.findall(r"(\d+),(\d+)", info.get("BESTLINE", ""))
                            now = time.monotonic()
                            if progress is not None and pv_index == 0 and pv and now-published_at >= 0.15:
                                progress(result(tuple(map(int, pv[0]))))
                                published_at = now
                        else:
                            pv_index = int(value)
                            if pv_index == 0:
                                info.clear()
                    elif pv_index == 0:
                        info[key] = value

                self._send("\n".join(commands))
                try:
                    final, lines = self._until(lambda line: bool(re.fullmatch(r"\d+,\d+", line)),
                                               budget_ms/1000 + 5, receive_info, cancel)
                except TimeoutError:
                    self._send("STOP")
                    final, lines = self._until(lambda line: bool(re.fullmatch(r"\d+,\d+", line)), 2)
                if any("Failed" in line or "failed" in line or "fallback" in line.lower()
                       for line in startup + lines):
                    raise RuntimeError("Rapfi 权重未成功加载：" + "; ".join(lines[-6:]))
                if cancel is not None and cancel.is_set():
                    raise AnalysisCancelled()
                return result(tuple(map(int, final.split(","))), final=True)
            except AnalysisCancelled:
                raise
            except Exception:
                self._close_unlocked()
                raise

    def _close_unlocked(self):
        process, self.process = self.process, None
        if process:
            try:
                if process.poll() is None:
                    process.stdin.write("END\n")
                    process.stdin.flush()
                    process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=2)
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
        self.ready = False

    def close(self):
        with self.lock:
            self._close_unlocked()
