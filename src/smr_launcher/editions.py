"""Explicit experimental gameplay options for independently prepared profiles."""
from pathlib import Path
import hashlib
import re
import sys
import xml.etree.ElementTree as ET

SUPPORTED_GAME = '981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0'


def difficulty_template() -> bytes:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    path = root / 'Definitive-SMR-Launcher/DSMRL_data/RRT_Difficulty.xml'
    data = path.read_bytes()
    if len(data) > 128 * 1024 or ET.fromstring(data).tag != 'RRTDifficultyLevels':
        raise ValueError('Bundled difficulty definitions are invalid')
    return data


def enable_editor(data: bytes) -> bytes:
    text = data.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.splitlines()
    sections = [i for i, line in enumerate(lines) if line.strip().casefold() == '[user settings]']
    if len(sections) != 1:
        raise ValueError('Settings need exactly one User Settings section')
    start = sections[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].strip().startswith('[')), len(lines))
    keys = [i for i in range(start, end) if re.match(r'^\s*EditorEnabled\s*=', lines[i], re.I)]
    if len(keys) > 1:
        raise ValueError('Duplicate EditorEnabled settings need inspection')
    if keys:
        lines[keys[0]] = 'EditorEnabled = 1'
    else:
        lines.insert(start, 'EditorEnabled = 1')
    return (newline.join(lines) + newline).encode('utf-8')


def apply_options(root: Path, options: dict) -> dict:
    if set(options) != {'editor', 'difficulty', 'parent'} or any(type(options[k]) is not bool for k in ('editor', 'difficulty')):
        raise ValueError('Invalid edition options')
    if not options['editor'] and not options['difficulty']:
        raise ValueError('Choose at least one edition option')
    if not re.fullmatch('[0-9a-f]{64}', options['parent']):
        raise ValueError('Invalid parent variant identity')
    result = dict(options)
    if options['editor']:
        ini = root / 'Settings.ini'
        if ini.stat().st_size > 256 * 1024:
            raise ValueError('Settings file exceeds the editor limit')
        ini.write_bytes(enable_editor(ini.read_bytes()))
        result['settings_sha256'] = hashlib.sha256(ini.read_bytes()).hexdigest()
    if options['difficulty']:
        # Refuse maps which already define this resource anywhere in their overlay.
        # A scenario-specific difficulty would otherwise silently win or be replaced.
        existing = [p for base in ('CustomAssets', 'UserMaps') for p in (root / base).rglob('*')
                    if p.name.casefold() == 'rrt_difficulty.xml']
        if existing:
            raise ValueError('This map already supplies difficulty definitions; retain its original rules')
        data = difficulty_template()
        target = root / 'CustomAssets/XML/RRT_Difficulty.xml'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        result['difficulty_sha256'] = hashlib.sha256(data).hexdigest()
    return result
