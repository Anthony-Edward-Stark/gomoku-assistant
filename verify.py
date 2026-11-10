"""Regression checks for engine tactics, observer state and optional real Tencent frames."""
import argparse
import json
import tempfile
import threading
import time
from pathlib import Path
import cv2
import numpy as np
from engine import Engine
from vision import detect_grid, read_board, read_marks, validate_position
from app import Observer


def position(black, white):
    board = [[0]*15 for _ in range(15)]
    for color, stones in ((1, black), (2, white)):
        for x, y in stones:
            board[y][x] = color
    return board


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=Path, help="Optional directory with the labelled Tencent capture fixtures")
    args = parser.parse_args()
    cv2.setNumThreads(1)
    checks = []
    if args.frames:
        frames = {}
        expected = {
            "tencent-empty.jpg": ([], []),
            "tencent-active.jpg": (["J10", "I9", "K9", "H8"], ["J9", "J8", "I7"]),
            "tencent-markers.jpg": (["I11", "J10", "I9", "K9", "H8", "L8", "G7"],
                                   ["H12", "K11", "J9", "J8", "I7", "M7"]),
            "tencent-current.jpg": (["G9", "F8", "H8", "F7", "G7", "G6"],
                                   ["H10", "E9", "I9", "G8", "I7", "I6"]),
        }
        for name, (black, white) in expected.items():
            image = cv2.imread(str(args.frames / name))
            assert image is not None, f"Missing real frame: {name}"
            start = time.perf_counter()
            grid = detect_grid(image)
            assert grid is not None, name
            board, uncertain = read_board(image, grid)
            observed = {color: sorted(chr(65+x)+str(15-y) for y, x in zip(*np.where(board == color)))
                        for color in (1, 2)}
            assert not uncertain and observed == {1: sorted(black), 2: sorted(white)}, (name, observed, uncertain)
            frames[name] = (image, grid, board)
            checks.append({"check": name, "status": "passed", "detect_and_read_ms": round((time.perf_counter()-start)*1000, 1)})
        image, grid, board = frames["tencent-markers.jpg"]
        assert read_marks(image, grid, board)["yellow"] == [[6, 8]]
        checks.append({"check": "last-move yellow cross does not change black stone", "status": "passed"})
        red_image, red_grid, red_board = frames["tencent-current.jpg"]
        assert read_marks(red_image, red_grid, red_board)["red"] == [[8, 9]]
        checks.append({"check": "real red cross recognized as white last move", "status": "passed"})
        # Add a red cross to a real white stone. This is a synthetic overlay, labelled here.
        red_overlay = image.copy()
        x, y = round(grid.x0+8*grid.dx), round(grid.y0+8*grid.dy)
        cv2.line(red_overlay, (x-18, y), (x+18, y), (0, 0, 245), 4)
        cv2.line(red_overlay, (x, y-18), (x, y+18), (0, 0, 245), 4)
        red_board, uncertain = read_board(red_overlay, grid)
        assert not uncertain and np.array_equal(board, red_board)
        assert [8, 8] in read_marks(red_overlay, grid, red_board)["red"]
        checks.append({"check": "synthetic red cross on white stone", "status": "passed"})
        assert detect_grid(np.full_like(image, 128)) is None
        try:
            read_board(np.full_like(image, 128), grid)
            raise AssertionError("Non-board image accepted")
        except ValueError:
            pass
        checks.append({"check": "non-board page rejects old calibration", "status": "passed"})

    engine = Engine()
    try:
        cases = [
            ("black immediate win", position([(x,7) for x in range(4,8)], [(3,7),(0,0),(2,0),(4,0)]), [8,7]),
            ("white immediate win", position([(3,7),(0,0),(2,0),(4,0),(6,0)], [(x,7) for x in range(4,8)]), [8,7]),
            ("white mandatory block", position([(x,7) for x in range(4,8)], [(3,7),(0,0),(2,0)]), [8,7]),
        ]
        for name, matrix, move in cases:
            result = engine.analyse(matrix, "freestyle", 500)
            assert result["move"] == move, (name, result)
            assert result["coordinate"] == "I8"
            checks.append({"check": name, "status": "passed", "engine_ms": result["engine_ms"]})
        if args.frames:
            result = engine.analyse(board.tolist(), "freestyle", 500)
            assert result["evaluation_text"] and "M" in result["evaluation_text"], result
            checks.append({"check": "real position with mate evaluation string", "status": "passed",
                           "evaluation_text": result["evaluation_text"], "engine_ms": result["engine_ms"]})
        renju = position([(6,7),(8,7),(7,6),(7,8)], [(0,0),(2,0),(4,0),(6,0)])
        result = engine.analyse(renju, "renju", 500)
        assert result["move"] != [7,7], result
        checks.append({"check": "black double-three forbidden under Renju", "status": "passed"})
        invalid = position([], [])
        invalid[7][7] = -1
        try:
            engine.analyse(invalid, "freestyle")
            raise AssertionError("Incomplete board accepted")
        except ValueError:
            pass
        checks.append({"check": "engine rejects incomplete board", "status": "passed"})
    finally:
        engine.close()

    class HeldEngine:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
        def analyse(self, *_, **kwargs):
            self.started.set()
            assert self.release.wait(5)
            return {"move": [0,0], "turn": 2, "coordinate": "A15", "engine_ms": 1}
        def close(self):
            pass
    with tempfile.TemporaryDirectory() as temp:
        observer = Observer(temp, "freestyle")
        observer.settings["my_color"] = 2
        if args.frames:
            observer.grid = grid
            held = HeldEngine()
            observer.engine = held
            metadata = {"captured_at": time.time(), "frame_id": "test"}
            observer.observe(image, metadata)
            assert observer.snapshot()["suggestion"] is None
            observer.observe(image, metadata)
            assert held.started.wait(1)
            observer.observe(np.full_like(image, 128), metadata)
            assert observer.snapshot()["suggestion"] is None
            held.release.set()
            deadline = time.monotonic()+2
            while observer.inflight is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert observer.snapshot()["status"] == "no_grid" and observer.snapshot()["suggestion"] is None
            checks.append({"check": "late engine reply rejected after page change", "status": "passed"})
        observer.state.update(status="ready", captured_at=time.time()-3, suggestion={"coordinate": "H8"}, board=[[0]*15]*15)
        assert observer.snapshot()["status"] == "stale" and observer.snapshot()["suggestion"] is None
        checks.append({"check": "stale USB frame clears recommendation", "status": "passed"})
        observer.configure({"my_color": 2, "budget_ms": 2000})
        restored = Observer(temp, "freestyle")
        assert restored.settings["budget_ms"] == 2000 and restored.settings["my_color"] == 2
        checks.append({"check": "settings restored on restart", "status": "passed"})
    result = {"checks": checks, "passed": len(checks)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
