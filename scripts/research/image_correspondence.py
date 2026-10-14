"""Validate an exact image match before deriving a current window-local point.

The result proves image correspondence only. Scene meaning, input authority,
capture freshness and one-use dispatch remain the caller's responsibility.
"""

import math
import re


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def point_for_frame(value, *, reference_sha256, frame, rectangle, offset):
    """Refuse malformed, unbound or scaled results; never return a fallback."""
    keys = {
        "schema",
        "reference_sha256",
        "input_sha256",
        "width",
        "height",
        "rectangle",
        "point",
        "claim",
        "runtime_authority_granted",
    }
    if not (
        type(value) is dict
        and set(value) == keys
        and value["schema"] == "smr-image-control-correspondence/1"
        and type(reference_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", reference_sha256)
        and value["reference_sha256"] == reference_sha256
        and type(frame.get("sha256")) is str
        and re.fullmatch(r"[0-9a-f]{64}", frame["sha256"])
        and value["input_sha256"] == frame["sha256"]
        and value["claim"] == "image_correspondence_only"
        and value["runtime_authority_granted"] is False
    ):
        raise ValueError("Unbound image correspondence")
    width, height = frame.get("pixel_width"), frame.get("pixel_height")
    if not (
        type(width) is int
        and type(height) is int
        and 0 < width <= 8192
        and 0 < height <= 8192
        and width * height <= 16 * 1024 * 1024
        and type(value["width"]) is int
        and value["width"] == width
        and type(value["height"]) is int
        and value["height"] == height
        and type(rectangle) is list
        and len(rectangle) == 4
        and all(type(n) is int for n in rectangle)
        and 4 <= rectangle[2] <= 128
        and 4 <= rectangle[3] <= 128
        and 0 <= rectangle[0] <= width - rectangle[2]
        and 0 <= rectangle[1] <= height - rectangle[3]
        and type(offset) is list
        and len(offset) == 2
        and all(_finite(n) for n in offset)
        and 0 <= offset[0] < rectangle[2]
        and 0 <= offset[1] < rectangle[3]
    ):
        raise ValueError("Invalid image reference geometry")
    found, point = value["rectangle"], value["point"]
    if not (
        type(found) is list
        and len(found) == 4
        and all(type(n) is int for n in found)
        and found[2:] == rectangle[2:]
        and 0 <= found[0] <= width - found[2]
        and 0 <= found[1] <= height - found[3]
        and type(point) is list
        and len(point) == 2
        and all(_finite(n) for n in point)
        and point == [found[0] + offset[0], found[1] + offset[1]]
    ):
        raise ValueError("Invalid current image match")
    scale, window = frame.get("scale"), frame.get("window")
    if not (
        _finite(scale)
        and 0 < scale <= 4
        and type(window) is dict
        and all(_finite(window.get(k)) and window[k] > 0 for k in ("width", "height"))
        and abs(width - window["width"] * scale) <= 2
        and abs(height - window["height"] * scale) <= 2
    ):
        raise ValueError("Unbound image/window geometry")
    local = [point[0] / scale, point[1] / scale]
    if not (0 <= local[0] < window["width"] and 0 <= local[1] < window["height"]):
        raise ValueError("Current point outside window")
    return tuple(local)
