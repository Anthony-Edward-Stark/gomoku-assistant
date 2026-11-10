"""Search-budget comparison and interruption checks on the bundled Rapfi engine."""
import argparse
import json
import tempfile
import threading
import time
from pathlib import Path

import cv2
from app import Observer
from engine import AnalysisCancelled, BUDGETS, Engine
from vision import detect_grid, read_board


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    comparisons = []
    matrices = {}
    for name in ("tencent-active.jpg", "tencent-current.jpg", "tencent-markers.jpg"):
        image = cv2.imread(str(args.frames / name))
        grid = detect_grid(image)
        board, uncertain = read_board(image, grid)
        assert not uncertain
        matrix = matrices[name] = board.tolist()
        entry = {"frame": name, "source": "real Tencent frame"}
        for label, budget, threads, memory in (("previous", 2000, 2, 128), ("deep", 10000, 4, 512)):
            engine = Engine()
            updates = []
            try:
                result = engine.analyse(matrix, "freestyle", budget, threads=threads, memory_mb=memory,
                                        progress=lambda value: updates.append(dict(value)))
                assert result["final"] and updates and all(not value["final"] for value in updates)
                assert all(matrix[value["move"][1]][value["move"][0]] == 0 for value in updates)
                entry[label] = {**result, "progress_updates": len(updates),
                                "first_suggestion_ms": updates[0]["engine_ms"]}
            finally:
                engine.close()
        comparisons.append(entry)
        print(json.dumps({"comparison": entry}, ensure_ascii=False), flush=True)

    engine = Engine()
    cancel = threading.Event()
    updates = []
    error = []
    received = threading.Event()

    def progress(value):
        updates.append(value)
        received.set()

    def analyse():
        try:
            engine.analyse(matrices["tencent-current.jpg"], "freestyle", 30000, progress=progress, cancel=cancel)
        except Exception as value:
            error.append(value)

    try:
        search = threading.Thread(target=analyse)
        search.start()
        assert received.wait(8), "No live search progress"
        original_pid = engine.process.pid
        start = time.monotonic()
        cancel.set()
        search.join(3)
        cancel_ms = round((time.monotonic()-start)*1000, 1)
        assert not search.is_alive() and len(error) == 1 and isinstance(error[0], AnalysisCancelled), error
        assert engine.process.pid == original_pid and engine.process.poll() is None
        repeated = engine.analyse(matrices["tencent-current.jpg"], "freestyle", 500)
        assert repeated["final"] and engine.process.pid == original_pid
        checks = [{"check": "30-second search cancels promptly and engine is reused", "status": "passed",
                   "cancel_ms": cancel_ms}]
    finally:
        engine.close()

    class StreamingEngine:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
        def analyse(self, board, rule, budget, *, progress, cancel):
            result = {"move": [0, 0], "turn": 1, "coordinate": "A15", "engine_ms": 1, "final": False}
            progress(dict(result))
            self.started.set()
            assert self.release.wait(3)
            progress(dict(result))  # A late callback must be discarded after invalidation.
            return {**result, "final": True}
        def close(self):
            pass

    with tempfile.TemporaryDirectory() as temp:
        observer = Observer(temp, "freestyle", args.frames / "tencent-current.jpg")
        observer.configure({"budget_ms": 30000, "my_color": 1})
        restored = Observer(temp, "freestyle")
        assert restored.settings["budget_ms"] == 30000
        for budget in BUDGETS:
            observer.configure({"budget_ms": budget})
        checks.append({"check": "all eight time options and persistence", "status": "passed"})
        held = StreamingEngine()
        observer.engine = held
        image = cv2.imread(str(args.frames / "tencent-current.jpg"))
        observer.observe(image, {"frame_id": "test"})
        observer.observe(image, {"frame_id": "test"})
        assert held.started.wait(2)
        assert observer.snapshot()["status"] == "deepening" and observer.snapshot()["suggestion"]
        observer.configure({"paused": True})
        held.release.set()
        deadline = time.monotonic()+2
        while observer.inflight is not None and time.monotonic() < deadline:
            time.sleep(.01)
        assert observer.snapshot()["status"] == "paused" and observer.snapshot()["suggestion"] is None
        checks.append({"check": "pause clears progress and discards late preview and final result", "status": "passed"})

    print(json.dumps({"checks": checks, "comparisons": comparisons,
                      "scope": "Search depth and nodes; no human rating or match win-rate measured"},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
