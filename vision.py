"""Grid and black/white stone recognition. Coordinates start at the image's top left."""
from dataclasses import asdict, dataclass
import cv2
import numpy as np


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    x1: float
    y1: float
    size: int
    width: int
    height: int
    source: str = "auto"

    @property
    def dx(self):
        return (self.x1 - self.x0) / (self.size - 1)

    @property
    def dy(self):
        return (self.y1 - self.y0) / (self.size - 1)

    def valid(self):
        return (self.size in (15, 19) and self.width > 0 and self.height > 0
                and 0 <= self.x0 < self.x1 < self.width
                and 0 <= self.y0 < self.y1 < self.height
                and min(self.dx, self.dy) >= 12
                and abs(self.dx / self.dy - 1) < 0.08)

    def to_dict(self):
        return asdict(self)


def _clusters(values, tolerance=2):
    groups = []
    for value in sorted(values):
        if groups and value - np.mean(groups[-1]) < tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return np.array([np.mean(group) for group in groups])


def _axis_candidates(points, size, short_side):
    candidates = []
    if len(points) < size - 3:
        return candidates
    for start in points:
        for other in points:
            pitch = other - start
            if pitch < short_side * 0.028 or pitch > short_side * 0.073:
                continue
            targets = start + np.arange(size) * pitch
            distances = np.abs(points[:, None] - targets[None, :])
            nearest = distances.min(axis=0)
            hits = nearest < max(1.8, pitch * 0.12)
            if hits.sum() < size - 2:
                continue
            # Refit all observed grid lines, rather than accumulating pitch error.
            indices = np.arange(size)[hits]
            positions = points[distances[:, hits].argmin(axis=0)]
            slope, intercept = np.polyfit(indices, positions, 1)
            error = float(np.sqrt(np.mean((positions - (intercept + indices * slope)) ** 2)))
            if error <= 1.8:
                candidates.append((int(hits.sum()) - error, intercept, slope))
    return sorted(candidates, reverse=True)[:10]


def detect_grid(image, size=15):
    h, w = image.shape[:2]
    scale = min(1.0, 1000 / max(h, w))
    resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 35, 100)
    short = min(gray.shape)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                            minLineLength=int(short * 0.34), maxLineGap=int(short * 0.06))
    if lines is None:
        return None
    xs, ys = [], []
    for x0, y0, x1, y1 in lines.reshape(-1, 4):
        if abs(y0 - y1) <= 2:
            ys.append((y0 + y1) / 2)
        if abs(x0 - x1) <= 2:
            xs.append((x0 + x1) / 2)
    xfits = _axis_candidates(_clusters(xs), size, short)
    yfits = _axis_candidates(_clusters(ys), size, short)
    best = None
    for sx, x, dx in xfits:
        for sy, y, dy in yfits:
            if abs(dx / dy - 1) > 0.04:
                continue
            grid = Grid(x / scale, y / scale, (x + dx * (size-1)) / scale,
                        (y + dy * (size-1)) / scale, size, w, h)
            if grid.valid():
                support = grid_support(image, grid)
                score = sx + sy + support * 5
                if support >= 0.50 and (best is None or score > best[0]):
                    best = (score, grid)
    return best[1] if best else None


def grid_support(image, grid):
    """Measure grid lines between intersections, where stones do not reach."""
    if image.shape[1] != grid.width or image.shape[0] != grid.height or not grid.valid():
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    hits = total = 0
    offset = max(3, round(min(grid.dx, grid.dy) * 0.13))
    for vertical in (False, True):
        for i in range(1, grid.size-1):
            for j in range(grid.size-1):
                x = round(grid.x0 + (i if vertical else j + 0.5) * grid.dx)
                y = round(grid.y0 + (j + 0.5 if vertical else i) * grid.dy)
                if not (offset < x < w-offset and offset < y < h-offset):
                    continue
                if vertical:
                    line = gray[y-1:y+2, x-2:x+3].min(axis=1).astype(float).mean()
                    bg = (float(gray[y, x-offset]) + float(gray[y, x+offset])) / 2
                else:
                    line = gray[y-2:y+3, x-1:x+2].min(axis=0).astype(float).mean()
                    bg = (float(gray[y-offset, x]) + float(gray[y+offset, x])) / 2
                hits += bg - line > 13
                total += 1
    return hits / max(total, 1)


def read_board(image, grid):
    """Use a ring around each intersection to ignore move-number/last-move badges.

    -1 means uncertain. No guess is sent to the engine when any cell is uncertain.
    Supports light wooden boards with ordinary black and white round stones.
    """
    if grid_support(image, grid) < 0.50:
        raise ValueError("棋盘线已变化或被遮挡")
    board = np.zeros((grid.size, grid.size), dtype=np.int8)
    uncertain = []
    radius = max(5, round(min(grid.dx, grid.dy) * 0.56))
    yy, xx = np.mgrid[-radius:radius+1, -radius:radius+1]
    distance = np.sqrt(xx**2 + yy**2) / min(grid.dx, grid.dy)
    ring = (distance >= 0.16) & (distance <= 0.32)
    # Outside the stone, and off the two crossing grid lines.
    outer = (distance >= 0.49) & (distance <= 0.55) & (np.abs(xx) > 4) & (np.abs(yy) > 4)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gray = cv2.copyMakeBorder(gray, radius, radius, radius, radius, cv2.BORDER_REPLICATE)
    hsv = cv2.copyMakeBorder(hsv, radius, radius, radius, radius, cv2.BORDER_REPLICATE)
    for row in range(grid.size):
        for col in range(grid.size):
            x = round(grid.x0 + col * grid.dx)
            y = round(grid.y0 + row * grid.dy)
            inner_radius = round(min(grid.dx, grid.dy) * 0.33)
            if x-inner_radius < 0 or y-inner_radius < 0 or x+inner_radius >= grid.width or y+inner_radius >= grid.height:
                uncertain.append([col, row])
                board[row, col] = -1
                continue
            patch = gray[y:y+2*radius+1, x:x+2*radius+1]
            local_hsv = hsv[y:y+2*radius+1, x:x+2*radius+1]
            sat = local_hsv[:, :, 1]
            hue = local_hsv[:, :, 0]
            marker = (((hue <= 12) | (hue >= 168)) & (sat > 65) & (local_hsv[:, :, 2] > 170)) | (
                (hue >= 20) & (hue <= 40) & (sat > 110) & (local_hsv[:, :, 2] > 200))
            sample = ring & ~marker
            inner = patch[sample].astype(float)
            if len(inner) < np.count_nonzero(ring) * 0.50:
                board[row, col] = -1
                uncertain.append([col, row])
                continue
            background = float(np.median(patch[outer]))
            bg_sat = float(np.median(sat[outer]))
            dark = float(np.mean(inner < 105))
            white = float(np.mean((inner > 208) & (sat[sample] < 45)))
            white_body = float(np.mean((inner > 145) & (sat[sample] < 60)))
            delta = float(np.median(inner)) - background
            if dark >= 0.72 and float(np.median(inner)) < 100:
                board[row, col] = 1
            elif ((white >= 0.70 and delta > 15) or (
                    white_body >= 0.75 and np.median(inner) > 175 and bg_sat > 55 and np.median(sat[sample]) < 30)):
                board[row, col] = 2
            elif dark > 0.35 or (white > 0.4 and delta > 10) or delta < -28:
                board[row, col] = -1
                uncertain.append([col, row])
    return board, uncertain


def read_marks(image, grid, board):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    radius = round(min(grid.dx, grid.dy) * 0.29)
    result = {"red": [], "yellow": []}
    for row, col in zip(*np.where(board > 0)):
        x, y = round(grid.x0 + col*grid.dx), round(grid.y0 + row*grid.dy)
        patch = hsv[y-radius:y+radius+1, x-radius:x+radius+1]
        hue, sat, value = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
        red = (sat > 65) & (value > 170) & ((hue <= 12) | (hue >= 168))
        yellow = (sat > 110) & (value > 200) & (hue >= 20) & (hue <= 40)
        for name, mask in (("red", red), ("yellow", yellow)):
            expected_color = 2 if name == "red" else 1
            if int(board[row, col]) == expected_color and np.count_nonzero(mask) > max(12, radius*2):
                result[name].append([int(col), int(row)])
    return result


def validate_position(board, rule):
    black = int(np.count_nonzero(board == 1))
    white = int(np.count_nonzero(board == 2))
    if np.any(board < 0):
        return {"status": "uncertain", "black": black, "white": white}
    if black not in (white, white+1):
        return {"status": "invalid_count", "black": black, "white": white}
    winner = None
    n = len(board)
    for row in range(n):
        for col in range(n):
            color = int(board[row, col])
            if color == 0:
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                px, py = col-dx, row-dy
                if 0 <= px < n and 0 <= py < n and board[py, px] == color:
                    continue
                length, x, y = 0, col, row
                while 0 <= x < n and 0 <= y < n and board[y, x] == color:
                    length += 1
                    x, y = x+dx, y+dy
                if (rule == "standard" and length == 5
                        or rule == "renju" and ((color == 1 and length == 5) or (color == 2 and length >= 5))
                        or rule == "freestyle" and length >= 5):
                    winner = color
                if rule == "renju" and color == 1 and length > 5:
                    return {"status": "black_overline", "black": black, "white": white}
    if winner:
        return {"status": "finished", "winner": winner, "black": black, "white": white}
    if black + white == n*n:
        return {"status": "draw", "black": black, "white": white}
    return {"status": "valid", "turn": 1 if black == white else 2, "black": black, "white": white}
