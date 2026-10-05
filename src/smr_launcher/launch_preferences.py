"""Select an imported scenario without changing map assets or saved games."""
from __future__ import annotations

from pathlib import Path
import re


class LaunchPreferencesError(ValueError):
    """Ambiguous settings must be inspected before launching."""


def prepare_settings(data: bytes, scenarios: tuple[str, ...]) -> bytes:
    """Preserve profile preferences; select this package and skip opening movies.

    Retain the player's last scenario when it belongs to a multi-scenario package.
    Quickstart must stay off because this game build uses a fixed stock scenario.
    This function is only for writable custom profiles, never frozen inputs.
    """
    if len(data) > 256 * 1024:
        raise LaunchPreferencesError("Settings file exceeds the launch preference limit")
    names = []
    for scenario in scenarios:
        path = Path(scenario)
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or path.parts[0] not in ("UserMaps", "CustomAssets")
                or path.suffix.casefold() != ".xml"
                or any(c in scenario for c in ("\r", "\n", "\0", "\\"))):
            raise LaunchPreferencesError("Invalid scenario path in map catalogue")
        names.append(path.name.lower())
    # This preference stores a basename, not a path. Repeated package paths do
    # not create different selectable names. Their asset-resolution ambiguity
    # remains a separate import diagnostic; this does not certify either file.
    names = list(dict.fromkeys(names))
    if not names:
        raise LaunchPreferencesError("Map needs a scenario before automatic selection")
    encoding = ("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else
                "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
    # Explicit endianness preserves either UTF-16 BOM exactly.
    if encoding == "utf-16":
        bom, payload = data[:2], data[2:]
        encoding = "utf-16-le" if bom == b"\xff\xfe" else "utf-16-be"
    else:
        bom, payload = b"", data
    try:
        text = payload.decode(encoding)
    except UnicodeError as exc:
        raise LaunchPreferencesError("Game settings encoding needs inspection") from exc
    lines = text.splitlines(keepends=True)
    sections = [i for i, line in enumerate(lines) if line.strip().casefold() == "[user settings]"]
    if len(sections) != 1:
        raise LaunchPreferencesError("Settings need exactly one User Settings section")
    start = sections[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    positions = {}
    for key in ("LastScenarioName", "SkipOpeningMovies", "Quickstart"):
        matches = [i for i in range(start, end) if re.match(r"^\s*" + key + r"\s*=", lines[i], re.I)]
        if len(matches) > 1:
            raise LaunchPreferencesError("Duplicate " + key + " settings need inspection")
        positions[key] = matches[0] if matches else None
    current_index = positions["LastScenarioName"]
    current = lines[current_index].split("=", 1)[1].strip().lower() if current_index is not None else ""
    values = dict(LastScenarioName=current if current in names else names[0],
                  SkipOpeningMovies="1", Quickstart="0")
    newline = "\r\n" if "\r\n" in text else "\n"
    additions = []
    for key, value in values.items():
        index = positions[key]
        if index is None:
            additions.append(key + " = " + value + newline)
        else:
            line = lines[index]
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            prefix = line.split("=", 1)[0] + "="
            lines[index] = prefix + " " + value + ending
    if additions:
        if end and not lines[end-1].endswith(("\n", "\r")):
            lines[end-1] += newline
        lines[end:end] = additions
    return bom + "".join(lines).encode(encoding)
