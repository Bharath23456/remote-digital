"""Conservative QR and bubble-grid recognition for scanned identity covers.

Unclear marks are rejected; this module never guesses a student's identity.
"""

import cv2
import numpy as np


class RecognitionError(ValueError):
    pass


# This cover's USN uses digit-only and letter-only columns, with both grids
# starting on the same row. It is not a 36-row alphanumeric grid.
USN_COLUMN_SYMBOLS = (
    "0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "0123456789", "0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", "0123456789", "0123456789",
)


def recognize_cover(content: bytes) -> tuple[str, str]:
    image = _decode_cover(content)
    qr, points = _read_qr(image)
    usn = _read_usn_grid(image, points)
    return qr, usn


def read_cover_qr_if_present(content: bytes) -> str | None:
    image = _decode_cover(content, minimum_dimension=300)
    try:
        qr, _ = _read_qr(image)
        return qr
    except RecognitionError:
        return None


def _decode_cover(content: bytes, *, minimum_dimension: int = 700) -> np.ndarray:
    if not 100 <= len(content) <= 12_000_000:
        raise RecognitionError("Front-page image must be between 100 bytes and 12 MB")
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.shape[0] < minimum_dimension or image.shape[1] < minimum_dimension or image.size > 30_000_000:
        raise RecognitionError("Upload a clear, complete front-page image")
    scale = 2000 / image.shape[1]
    if abs(scale - 1) > 0.01:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    return image


def _read_qr(image: np.ndarray) -> tuple[str, np.ndarray]:
    qr, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
    qr = qr.strip()
    if not qr or points is None or len(qr) > 64:
        raise RecognitionError("Booklet QR could not be read; rescan the front page")
    return qr, points[0]


def _clusters(values: list[int], tolerance: int) -> list[tuple[int, int]]:
    groups: list[list[int]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [(round(float(np.median(group))), len(group)) for group in groups]


def _read_usn_grid(image: np.ndarray, qr_points: np.ndarray) -> str:
    height, width = image.shape
    qr_left = float(qr_points[:, 0].min())
    qr_bottom = float(qr_points[:, 1].max())
    left = max(0, int(qr_left - width * 0.25))
    top = min(height - 1, int(qr_bottom + width * 0.04))
    crop = image[top:, left:]
    circles = cv2.HoughCircles(
        cv2.GaussianBlur(crop, (5, 5), 0), cv2.HOUGH_GRADIENT,
        dp=1.3, minDist=30, param1=120, param2=24,
        minRadius=15, maxRadius=33,
    )
    if circles is None:
        raise RecognitionError("USN bubbles were not found; rescan the complete front page")
    centers = [(int(x) + left, int(y) + top) for x, y, _ in circles[0]]
    row_groups = _clusters([y for _, y in centers], 14)
    rows = [y for y, count in row_groups if count >= 8]
    runs = []
    for row in rows:
        if not runs or not 32 <= row - runs[-1][-1] <= 85:
            runs.append([row])
        else:
            runs[-1].append(row)
    grid_rows = max(runs, key=len, default=[])
    if len(grid_rows) < 8:
        raise RecognitionError("USN grid could not be aligned; rescan the complete front page")
    row_step = float(np.median(np.diff(grid_rows[: min(10, len(grid_rows))])))
    if not 32 <= row_step <= 85:
        raise RecognitionError("USN grid spacing is invalid; rescan the front page")
    first_row = grid_rows[0]
    first_centers = [x for x, y in centers if any(abs(y - row) <= 12 for row in grid_rows[:4])]
    columns = [x for x, count in _clusters(first_centers, 12) if count >= 2]
    if len(columns) > 10:
        columns = columns[-10:]
    if len(columns) != 10:
        raise RecognitionError("Expected ten USN columns; check the page orientation")
    if first_row + row_step * (max(map(len, USN_COLUMN_SYMBOLS)) - 1) + 20 > height:
        raise RecognitionError("The USN grid is cut off; upload the complete front page")
    values = []
    for x, symbols in zip(columns, USN_COLUMN_SYMBOLS):
        darkness = []
        for index in range(len(symbols)):
            y = round(first_row + index * row_step)
            cell = image[y - 10:y + 10, x - 10:x + 10]
            darkness.append(float(np.mean(cell < 90)))
        order = np.argsort(darkness)[::-1]
        best, runner_up = int(order[0]), int(order[1])
        if darkness[best] < 0.58 or darkness[best] - darkness[runner_up] < 0.28:
            raise RecognitionError("USN has an unfilled or ambiguous bubble; rescan the front page")
        values.append(symbols[best])
    return "".join(values)
