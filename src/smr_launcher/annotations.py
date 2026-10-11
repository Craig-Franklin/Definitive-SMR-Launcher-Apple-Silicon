"""Private display overrides, separate from map packages and verification facts."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import zlib

from .cache_files import open_parent, replace_bytes

MAX_IMAGE_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 1500 * 1024


def validate_image(value):
    if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("Choose a PNG image of at most 1 MiB and 360 × 360 pixels")
    try:
        image = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError("Image is not valid base64 PNG data") from None
    if not image or image[:8] != b"\x89PNG\r\n\x1a\n" or len(image) > MAX_IMAGE_BYTES:
        raise ValueError("Choose a PNG image of at most 1 MiB")
    offset, first, data_seen, ended = 8, True, False, False
    while offset + 12 <= len(image):
        size = struct.unpack(">I", image[offset : offset + 4])[0]
        kind = image[offset + 4 : offset + 8]
        end = offset + size + 12
        if end > len(image):
            raise ValueError("PNG chunk is incomplete")
        chunk = image[offset + 8 : end - 4]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != struct.unpack(">I", image[end - 4 : end])[0]:
            raise ValueError("PNG checksum does not match")
        if first:
            if kind != b"IHDR" or size != 13:
                raise ValueError("PNG header is missing")
            width, height = struct.unpack(">II", chunk[:8])
            if not 0 < width <= 360 or not 0 < height <= 360:
                raise ValueError("Choose a PNG image of at most 360 × 360 pixels")
        elif kind == b"IHDR":
            raise ValueError("PNG has duplicate headers")
        if kind == b"IDAT":
            data_seen = True
        if kind == b"IEND":
            ended = size == 0 and end == len(image)
            break
        first, offset = False, end
    if not ended or not data_seen:
        raise ValueError("PNG image data is incomplete")
    return value


class AnnotationStore:
    def __init__(self, library):
        self.root = Path(library).expanduser().absolute() / "map-annotations"
        if ".." in self.root.parts:
            raise ValueError("Annotation library path must not contain traversal")

    def _path(self, record):
        identities = (record.archive_sha256, record.variant_id)
        if not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in identities
        ):
            raise ValueError("Map annotation requires exact archive and variant identities")
        key = hashlib.sha256("\n".join(identities).encode()).hexdigest()
        return self.root / (key + ".json")

    @staticmethod
    def _validate(value):
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise ValueError("Map annotation needs inspection")
        for key, limit in (("title", 256), ("notes", 16000)):
            text = value.get(key)
            if (
                not isinstance(text, str)
                or len(text) > limit
                or any(ord(c) < 32 and c not in "\n\r\t" for c in text)
            ):
                raise ValueError("Invalid personal " + key)
        image = value.get("image_png")
        if image:
            validate_image(image)
        elif image != "":
            raise ValueError("Invalid personal image")
        return {key: value[key] for key in ("title", "notes", "image_png")}

    @staticmethod
    def _private_parent(parent):
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(parent)
            raise ValueError("Map annotation directory must be private and owned by this user")
        return parent

    def load(self, record):
        path = self._path(record)
        parent = fd = None
        try:
            _, opened = open_parent(path)
            parent = self._private_parent(opened)
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_DOCUMENT_BYTES
            ):
                raise ValueError("Map annotation is not a bounded independent regular file")
            with os.fdopen(fd, "rb") as stream:
                fd = None
                payload = stream.read(MAX_DOCUMENT_BYTES + 1)
            if len(payload) > MAX_DOCUMENT_BYTES:
                raise ValueError("Map annotation is too large")
            return self._validate(json.loads(payload))
        except FileNotFoundError:
            return dict(title="", notes="", image_png="")
        finally:
            if fd is not None:
                os.close(fd)
            if parent is not None:
                os.close(parent)

    def save(self, record, *, title, notes, image_png=None):
        previous = self.load(record)
        value = dict(
            schema=1,
            title=title,
            notes=notes,
            image_png=previous["image_png"] if image_png is None else image_png,
        )
        result = self._validate(value)
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise ValueError("Map annotation is too large")
        path, parent = open_parent(self._path(record), create=True)
        parent = self._private_parent(parent)
        replace_bytes(
            path,
            parent,
            payload,
            prefix=".annotation-",
            invalid_destination=ValueError("Map annotation destination is unsafe"),
        )
        return result
