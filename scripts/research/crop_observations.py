"""Strict crop projection adapter; source coordinates alone never authorize input."""

from scene_contract import text_observations


def crop_text_observations(value, *, need, image_hash, width, height, revision, labels):
    """Return exact requested rows in full-source normalized lower-left coordinates."""
    need(
        type(width) is int
        and type(height) is int
        and 20 <= width <= 8192
        and 20 <= height <= 8192
        and width * height <= 16 * 1024 * 1024,
        "Invalid crop source dimensions",
    )
    x, y = width // 20, height // 20
    crop_width, crop_height = width - 2 * x, height - 2 * y
    expected = [x, y, crop_width, crop_height]
    fields = {
        "schema",
        "input_sha256",
        "width",
        "height",
        "recognition_revision",
        "coordinate_space",
        "matches",
        "source_width",
        "source_height",
        "crop_rectangle",
    }
    need(
        type(value) is dict
        and set(value) == fields
        and value["schema"] == "smr-owned-crop-text/1"
        and value["coordinate_space"] == "normalized-crop-lower-left"
        and type(value["source_width"]) is int
        and value["source_width"] == width
        and type(value["source_height"]) is int
        and value["source_height"] == height
        and type(value["crop_rectangle"]) is list
        and len(value["crop_rectangle"]) == 4
        and all(type(v) is int for v in value["crop_rectangle"])
        and value["crop_rectangle"] == expected,
        "Unbound or malformed crop projection",
    )
    # Reuse the unchanged row/hash/revision validator in its declared crop canvas.
    projection = {k: value[k] for k in fields - {"source_width", "source_height", "crop_rectangle"}}
    projection.update(
        schema="smr-owned-image-text/1", coordinate_space="normalized-image-lower-left"
    )
    rows = text_observations(
        projection,
        need=need,
        image_hash=image_hash,
        width=crop_width,
        height=crop_height,
        revision=revision,
        labels=labels,
    )
    source_rows = [
        dict(
            row,
            x=(x + row["x"] * crop_width) / width,
            y=(height - y - crop_height + row["y"] * crop_height) / height,
            width=row["width"] * crop_width / width,
            height=row["height"] * crop_height / height,
        )
        for row in rows
    ]
    # Validate the transformed full-source geometry too, including underflow.
    projection.update(width=width, height=height, matches=source_rows)
    return text_observations(
        projection,
        need=need,
        image_hash=image_hash,
        width=width,
        height=height,
        revision=revision,
        labels=labels,
    )
