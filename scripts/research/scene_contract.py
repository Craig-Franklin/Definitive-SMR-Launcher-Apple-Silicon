"""Closed one-click scene policy and strict offline OCR projection validation."""

from collections import Counter
import math

PURPOSE = "UK_FILENAME_EFFECT_OBSERVATION_V3"
POLICY = {
    "schema": 1,
    "purpose": PURPOSE,
    "target": "Single Player",
    "minimum_confidence": 0.95,
    "reserved_after_startup": 10,
    "maximum_scene_clicks": 2,
    "implicit_native_activation_admitted": True,
    "oracle_sha256": "3e97ebf7e67e2916ddf4284e53668f1bbcdd6dfa89c7feba57a1c319cd34c446",
}
HELPER_SOURCE_SHA = "fdcb0cff20ee8f19a72e07341f5705d4597d34ab646b8995ac34a7e047622431"
HELPER_BINARY_SHA = "71e12806cde0d77578919c1e0dc4519a4b505d600ae589771c44c95530cb5336"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def text_observations(value, *, need, image_hash, width, height, revision, labels):
    """Validate every field before returning all exact requested observations."""
    need(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "input_sha256",
            "width",
            "height",
            "recognition_revision",
            "coordinate_space",
            "matches",
        }
        and value["schema"] == "smr-owned-image-text/1"
        and type(value["input_sha256"]) is str
        and value["input_sha256"] == image_hash
        and type(value["width"]) is int
        and value["width"] == width
        and type(value["height"]) is int
        and value["height"] == height
        and type(value["recognition_revision"]) is int
        and value["recognition_revision"] == revision
        and value["coordinate_space"] == "normalized-image-lower-left"
        and type(value["matches"]) is list
        and len(value["matches"]) <= 128,
        "Unbound or malformed image-text observation",
    )
    for row in value["matches"]:
        need(
            type(row) is dict
            and set(row) == {"text", "confidence", "x", "y", "width", "height"}
            and type(row["text"]) is str
            and row["text"] in labels
            and all(finite(row[k]) for k in ("confidence", "x", "y", "width", "height"))
            and 0 <= row["confidence"] <= 1
            and row["x"] >= 0
            and row["y"] >= 0
            and row["width"] > 0
            and row["height"] > 0
            and row["x"] + row["width"] <= 1
            and row["y"] + row["height"] <= 1,
            "Invalid image-text match",
        )
    return value["matches"]


def menu_target(value, frame, projection, labels, *, need):
    rows = text_observations(
        value,
        need=need,
        image_hash=frame["sha256"],
        width=frame["pixel_width"],
        height=frame["pixel_height"],
        revision=projection["recognition_revision"],
        labels=labels,
    )
    expected = Counter(row["text"] for row in projection["matches"])
    need(
        len(expected) >= 2
        and expected[POLICY["target"]] == 1
        and Counter(row["text"] for row in rows) == expected
        and all(row["confidence"] >= POLICY["minimum_confidence"] for row in rows),
        "Observed main-menu signature/unique high-confidence target not established",
    )
    (target,) = [row for row in rows if row["text"] == POLICY["target"]]
    scale = frame["scale"]
    window = frame["window"]
    need(finite(scale) and 0 < scale <= 4, "Actual screenshot scale required")
    px = (target["x"] + target["width"] / 2) * frame["pixel_width"]
    py = (1 - target["y"] - target["height"] / 2) * frame["pixel_height"]
    x, y = px / scale, py / scale
    need(
        all(finite(v) for v in (px, py, x, y))
        and 0 <= px < frame["pixel_width"]
        and 0 <= py < frame["pixel_height"]
        and 0 <= x < window["width"]
        and 0 <= y < window["height"],
        "Observed image/window center out of bounds",
    )
    return x, y
