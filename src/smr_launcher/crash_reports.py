"""Bounded, offline macOS crash parsing and durable private incident retention.

No discovery, process inspection or publication occurs here. Callers supply session
identity and trusted build/map context. Raw diagnostic text never enters a payload.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone
import uuid

from .crash_details import extract_details, validate_details

MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
_U64 = (1 << 64) - 1
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}\Z")
_LAUNCHER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)\.?[0-9]+)?\Z")
_EXCEPTIONS = frozenset("EXC_BAD_ACCESS EXC_BAD_INSTRUCTION EXC_ARITHMETIC EXC_BREAKPOINT EXC_CRASH EXC_RESOURCE EXC_GUARD EXC_CORPSE_NOTIFY".split())
_SIGNALS = frozenset("SIGSEGV SIGBUS SIGILL SIGABRT SIGFPE SIGTRAP SIGKILL".split())
_NAMESPACES = frozenset("SIGNAL CODESIGNING WATCHDOG RESOURCE DYLD".split())
_KEYS = frozenset("schema fingerprint report_sha256 occurred_at game_executable_sha256 launcher_version game_version macos_version architecture translated archive_sha256 variant_id scenario_key exception_type signal termination_namespace exception_codes frames".split())


class CrashReportError(ValueError):
    """Invalid or unsafe input; messages intentionally contain no diagnostic data."""


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise CrashReportError("duplicate JSON key")
        out[key] = value
    return out


def _json(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(CrashReportError("nonfinite JSON")))
    except (ValueError, RecursionError) as exc:
        raise CrashReportError("invalid JSON") from exc


def _time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and len(value) <= 80:
        try:
            # Legacy macOS uses a space before its numeric timezone offset.
            # Normalize only this explicit format; never accept naive timestamps.
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})? [+-][0-9]{4}", value):
                value = value[:-6] + value[-5:]
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    try:
        if result.tzinfo is None or result.utcoffset() is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _uint(value):
    return value if type(value) is int and 0 <= value <= _U64 else None


def _uuid(value):
    try:
        if not isinstance(value, str) or len(value) not in (32, 36):
            return None
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _enum(value, choices):
    return value if isinstance(value, str) and value in choices else "UNKNOWN"


def _version(value):
    return value if isinstance(value, str) and len(value) <= 32 and _VERSION.fullmatch(value) else "UNKNOWN"


def _hash(value, *, required=False):
    if isinstance(value, str) and _HASH.fullmatch(value):
        return value
    if value is not None or required:
        raise CrashReportError("invalid context digest")
    return None


def _codes(values):
    if not isinstance(values, list):
        return []
    result = []
    for value in values[:8]:
        if isinstance(value, str) and re.fullmatch(r"(?:0x[0-9a-fA-F]{1,16}|[0-9]{1,20})", value):
            value = int(value, 16 if value.startswith("0x") else 10)
        if _uint(value) is not None:
            result.append(value)
    return result


def _modern(text):
    decoder = json.JSONDecoder(object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(CrashReportError("nonfinite JSON")))
    try:
        header, end = decoder.raw_decode(text.lstrip())
        rest = text.lstrip()[end:].strip()
        report = _json(rest) if rest else header
        if not isinstance(header, dict) or not isinstance(report, dict):
            raise CrashReportError("invalid report object")
        if rest and header.get("bug_type") not in (None, "309", 309):
            return None
        exception = report.get("exception", {})
        termination = report.get("termination")
        if termination is None:
            termination = {}
        if not isinstance(exception, dict) or not isinstance(termination, dict):
            raise CrashReportError("invalid exception object")
        bundle = report.get("bundleInfo", {})
        if not isinstance(bundle, dict):
            raise CrashReportError("invalid bundle object")
        os_version = report.get("osVersion", {})
        if not isinstance(os_version, dict):
            raise CrashReportError("invalid OS object")
        images, threads = report.get("usedImages", []), report.get("threads", [])
        if not isinstance(images, list) or not isinstance(threads, list):
            raise CrashReportError("invalid frame container")
        frames = []
        fault = report.get("faultingThread")
        selected = [t for i, t in enumerate(threads) if isinstance(t, dict) and
                    (t.get("triggered") is True or (type(fault) is int and i == fault))]
        if len(selected) == 1:
            source_frames = selected[0].get("frames", [])
            if not isinstance(source_frames, list):
                raise CrashReportError("invalid frames")
            for index, frame in enumerate(source_frames[:20]):
                if not isinstance(frame, dict):
                    raise CrashReportError("invalid frame")
                image_index = frame.get("imageIndex")
                image = images[image_index] if type(image_index) is int and 0 <= image_index < len(images) else {}
                if not isinstance(image, dict):
                    image = {}
                frames.append((index, image.get("path"), _uuid(image.get("uuid")), _uint(frame.get("imageOffset"))))
        return dict(diagnostic_source=report, image_uuids=[_uuid(i.get("uuid")) for i in images if isinstance(i, dict) and i.get("path") == report.get("procPath")], path=report.get("procPath"), bundle=bundle.get("CFBundleIdentifier"),
                    pid=report.get("pid"), when=report.get("captureTime", header.get("timestamp")),
                    birth=report.get("procLaunch"), exception=exception.get("type"),
                    signal=exception.get("signal"), namespace=termination.get("namespace"),
                    codes=_codes(exception.get("rawCodes", [])), frames=frames,
                    game_version=bundle.get("CFBundleShortVersionString", header.get("app_version")),
                    macos_version=os_version.get("train"), arch={"X86-64": "x86_64", "ARM-64": "arm64"}.get(report.get("cpuType"), report.get("cpuType")),
                    translated=report.get("translated"), normal=termination.get("code") == 0 and exception.get("type") is None)
    except (ValueError, RecursionError, IndexError, TypeError) as exc:
        raise CrashReportError("invalid modern crash report") from exc


def _legacy(text):
    # Header fields are singletons: duplicates must not let a later field replace identity.
    fields = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z /-]+):\s*(.*)$", line)
        if match and match[1] in {"Process", "Path", "Identifier", "Date/Time", "Launch Time", "Exception Type", "Exception Codes", "Termination Reason", "Version", "OS Version", "Code Type", "Crashed Thread"}:
            if match[1] in fields:
                raise CrashReportError("duplicate legacy identity field")
            fields[match[1]] = match[2]
    if not {"Process", "Path", "Date/Time"} <= fields.keys():
        raise CrashReportError("unrecognized legacy report")
    process = re.fullmatch(r".* \[([0-9]{1,10})\]", fields.get("Process", ""))
    exception = re.fullmatch(r"(EXC_[A-Z_]+)(?: \((SIG[A-Z]+)\))?", fields.get("Exception Type", ""))
    namespace = re.search(r"Namespace ([A-Z]+)", fields.get("Termination Reason", ""))
    os_version = re.search(r"(?:Mac OS X|macOS) ([0-9.]+)", fields.get("OS Version", ""))
    version = re.match(r"([0-9.]+)(?: |$)", fields.get("Version", ""))
    images = []
    for line in text.splitlines():
        match = re.match(r"\s*(0x[0-9a-fA-F]{1,16})\s*-\s*(0x[0-9a-fA-F]{1,16})\s+.*?<([0-9a-fA-F-]{32,36})>\s+(.+)$", line)
        if match:
            images.append((int(match[1], 16), int(match[2], 16), _uuid(match[3]), match[4]))
    frames, active = [], False
    for line in text.splitlines():
        if re.match(r"Thread [0-9]+ Crashed:", line):
            if active or frames:
                raise CrashReportError("multiple crashed threads")
            active = True
            continue
        if active and (not line.strip() or line.startswith("Thread ")):
            active = False
        match = re.match(r"\s*([0-9]+)\s+\S+\s+(0x[0-9a-fA-F]{1,16})\s+", line) if active else None
        if match and len(frames) < 20:
            address = int(match[2], 16)
            found = [image for image in images if image[0] <= address <= image[1]]
            image = found[0] if len(found) == 1 else None
            frames.append((len(frames), image[3] if image else None, image[2] if image else None,
                           _uint(address - image[0]) if image else None))
    raw_codes = re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b", fields.get("Exception Codes", ""))
    return dict(image_uuids=[i[2] for i in images if i[3] == fields.get("Path")], path=fields.get("Path"), bundle=fields.get("Identifier"), pid=int(process[1]) if process else None,
                when=fields.get("Date/Time"), birth=fields.get("Launch Time"),
                exception=exception[1] if exception else None, signal=exception[2] if exception else None,
                namespace=namespace[1] if namespace else None, codes=_codes(raw_codes), frames=frames,
                game_version=version[1] if version else None, macos_version=os_version[1] if os_version else None,
                arch={"X86-64": "x86_64", "ARM-64": "arm64"}.get(fields.get("Code Type", "").split(" ")[0]),
                translated=True if "(Translated)" in fields.get("Code Type", "") else False if "(Native)" in fields.get("Code Type", "") else None, normal=False)


def parse_crash_report(raw: bytes, *, expected_executable: str, expected_pid: int | None = None,
                       session_started_at: datetime | None = None, context: dict | None = None) -> dict | None:
    """Return a public payload for a confirmed crash of the supplied session.

    Confirmation requires exact path/PID, aware session start and a report launch
    timestamp. context process_started_at, when supplied, pins birth; an explicit
    process_birth_tolerance_us up to1000 supports OS timestamp precision. A trusted
    expected_redacted_path additionally requires exact bundle and image UUID;
    expected_bundle_id and session_ended_at add further correlation. Required
    context: game_executable_sha256 and official launcher_version. Missing birth,
    path, PID, timestamp or crash cause remains unconfirmed (None).
    """
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_REPORT_BYTES:
        raise CrashReportError("invalid report size")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CrashReportError("invalid report encoding") from exc
    if not isinstance(expected_executable, str) or not os.path.isabs(expected_executable):
        raise CrashReportError("expected executable must be absolute")
    if context is not None and not isinstance(context, dict):
        raise CrashReportError("invalid context")
    context = context or {}
    report = _modern(text) if text.lstrip().startswith("{") else _legacy(text)
    if report is None:
        return None
    start, when, birth = _time(session_started_at), _time(report["when"]), _time(report["birth"])
    if type(expected_pid) is not int or expected_pid <= 0 or start is None:
        return None
    exact_path = report["path"] == expected_executable
    alias = context.get("expected_redacted_path")
    alias_uuid = _uuid(context.get("expected_image_uuid"))
    alias_bundle = context.get("expected_bundle_id")
    redacted_path = (isinstance(alias, str) and alias.startswith("/Users/USER/Library/Application Support/Steam/*/")
                     and report["path"] == alias and alias_uuid is not None
                     and report["image_uuids"] == [alias_uuid]
                     and isinstance(alias_bundle, str) and report["bundle"] == alias_bundle)
    if (not exact_path and not redacted_path) or type(report["pid"]) is not int or report["pid"] != expected_pid:
        return None
    if "expected_image_uuid" in context and (alias_uuid is None or report["image_uuids"] != [alias_uuid]):
        return None
    if when is None or birth is None or when < start or birth > when:
        return None
    expected_birth = _time(context.get("process_started_at")) if "process_started_at" in context else start
    tolerance = context.get("process_birth_tolerance_us", 0)
    if type(tolerance) is not int or not 0 <= tolerance <= 1000:
        raise CrashReportError("invalid process birth precision")
    if expected_birth is None or abs((birth - expected_birth).total_seconds()) * 1000000 > tolerance:
        return None
    if "session_ended_at" in context:
        end = _time(context["session_ended_at"])
        if end is None or when > end:
            return None
    if "expected_bundle_id" in context and report["bundle"] != context["expected_bundle_id"]:
        return None
    if report["signal"] in ("SIGKILL", "SIGTERM"):
        return None  # Forced/ordinary termination is retained locally, never auto-reported as a crash.
    exception = _enum(report["exception"], _EXCEPTIONS)
    signal = _enum(report["signal"], _SIGNALS)
    namespace = _enum(report["namespace"], _NAMESPACES)
    if report["normal"] or (exception == "UNKNOWN" and signal == "UNKNOWN"):
        return None
    launcher = context.get("launcher_version")
    if not isinstance(launcher, str) or len(launcher) > 48 or not _LAUNCHER.fullmatch(launcher):
        raise CrashReportError("invalid launcher version")
    frames = []
    for index, path, image_uuid, offset in report["frames"]:
        role = "game" if path == expected_executable or (redacted_path and path == alias) else "system" if isinstance(path, str) and path.startswith(("/System/Library/", "/usr/lib/")) else "other"
        frames.append(dict(index=index, image_role=role, image_uuid=image_uuid, offset=offset))
    payload = dict(schema=2, fingerprint="", report_sha256=hashlib.sha256(raw).hexdigest(),
                   occurred_at=when.isoformat(), game_executable_sha256=_hash(context.get("game_executable_sha256"), required=True),
                   launcher_version=launcher, game_version=_version(report["game_version"]),
                   macos_version=_version(report["macos_version"]), architecture=_enum(report["arch"], {"x86_64", "arm64"}),
                   translated=report["translated"] if type(report["translated"]) is bool else None,
                   archive_sha256=_hash(context.get("archive_sha256")), variant_id=_hash(context.get("variant_id")),
                   scenario_key=_hash(context.get("scenario_key")), exception_type=exception, signal=signal,
                   termination_namespace=namespace, exception_codes=report["codes"], frames=frames)
    # Fault addresses/codes can vary with ASLR; module UUID + relative offset do not.
    signature = {key: payload[key] for key in ("game_executable_sha256", "archive_sha256", "variant_id", "scenario_key", "exception_type", "signal", "termination_namespace")}
    signature["signature_version"] = 2
    # Rosetta may describe a translated PC as an address-like offset in a
    # zero-UUID image. Retain it diagnostically, but never treat it as a stable
    # module-relative identity. Prefer the ordered original game frames.
    known = [frame for frame in frames if frame["image_uuid"] not in (None, "00000000-0000-0000-0000-000000000000")
             and frame["offset"] is not None]
    selected = [frame for frame in known if frame["image_role"] == "game"] or known
    signature["frames"] = [{key: frame[key] for key in ("image_role", "image_uuid", "offset")} for frame in selected]
    payload["fingerprint"] = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    try:
        payload["diagnostics"] = validate_details(extract_details(report.get("diagnostic_source", {}),
            alias if redacted_path else expected_executable, legacy="diagnostic_source" not in report))
    except (ValueError, TypeError, RecursionError) as exc:
        raise CrashReportError("invalid structured diagnostics") from exc
    return payload


def _validate_payload(payload):
    if type(payload) is not dict or type(payload.get("schema")) is not int or payload["schema"] not in (1, 2) or set(payload) != (_KEYS | {"diagnostics"} if payload["schema"] == 2 else _KEYS):
        raise CrashReportError("invalid payload keys")
    for name in ("fingerprint", "report_sha256", "game_executable_sha256"):
        _hash(payload[name], required=True)
    for name in ("archive_sha256", "variant_id", "scenario_key"):
        _hash(payload[name])
    if not isinstance(payload["occurred_at"], str) or _time(payload["occurred_at"]) is None:
        raise CrashReportError("invalid payload timestamp")
    if not isinstance(payload["launcher_version"], str) or len(payload["launcher_version"]) > 48 or not _LAUNCHER.fullmatch(payload["launcher_version"]):
        raise CrashReportError("invalid launcher version")
    for name in ("game_version", "macos_version"):
        if payload[name] != "UNKNOWN" and _version(payload[name]) == "UNKNOWN":
            raise CrashReportError("invalid version")
    for name, choices in (("architecture", {"x86_64", "arm64"}), ("exception_type", _EXCEPTIONS), ("signal", _SIGNALS), ("termination_namespace", _NAMESPACES)):
        if not isinstance(payload[name], str) or payload[name] not in choices | {"UNKNOWN"}:
            raise CrashReportError("invalid enumeration")
    if payload["translated"] is not None and type(payload["translated"]) is not bool:
        raise CrashReportError("invalid translation flag")
    codes = payload["exception_codes"]
    if not isinstance(codes, list) or len(codes) > 8 or any(_uint(code) is None for code in codes):
        raise CrashReportError("invalid exception codes")
    frames = payload["frames"]
    if not isinstance(frames, list) or len(frames) > 20:
        raise CrashReportError("invalid frame list")
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {"index", "image_role", "image_uuid", "offset"}:
            raise CrashReportError("invalid frame keys")
        if _uint(frame["index"]) is None or frame["image_role"] not in ("game", "system", "other"):
            raise CrashReportError("invalid frame identity")
        if frame["image_uuid"] is not None and _uuid(frame["image_uuid"]) != frame["image_uuid"]:
            raise CrashReportError("invalid image UUID")
        if frame["offset"] is not None and _uint(frame["offset"]) is None:
            raise CrashReportError("invalid frame offset")
    if payload["schema"] == 2:
        try:
            validate_details(payload["diagnostics"])
        except (ValueError, TypeError, RecursionError) as exc:
            raise CrashReportError("invalid structured diagnostics") from exc
    return json.loads(json.dumps(payload))


def _read_at(directory_fd, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise CrashReportError("unsafe private file")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > limit:
            raise CrashReportError("private file too large")
        return b"".join(chunks)
    finally:
        os.close(fd)


class CrashStore:
    """Private, immutable raw incidents with separately mutable publication state.

    Entries contain incident_id, fingerprint, report_sha256, raw_file (basename),
    payload, publication {status, issue_url, detail}. Duplicate identical bytes
    return the existing incident; different bytes retain separate incidents even
    with the same fingerprint. Exceptions are sanitized CrashReportError; callers
    must catch these at their passive-monitor boundary to keep play unaffected.
    """
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        # Reject symlink traversal in every existing ancestor, not only the leaf.
        for parent in reversed((self.root, *self.root.parents)):
            if parent.is_symlink():
                raise CrashReportError("symlink store path")
        try:
            self.root.mkdir(mode=0o700, parents=False, exist_ok=True)
            self._fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(self._fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                self.close()
                raise CrashReportError("store must be owner-only 0700")
        except OSError as exc:
            raise CrashReportError("cannot open private store") from exc

    def close(self):
        fd = getattr(self, "_fd", None)
        if fd is not None:
            os.close(fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()

    def _write(self, name, data, *, replace=False):
        temporary = ".tmp-" + uuid.uuid4().hex
        fd = None
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                _read_at(self._fd, name, MAX_METADATA_BYTES)
                os.replace(temporary, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            else:
                os.link(temporary, name, src_dir_fd=self._fd, dst_dir_fd=self._fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=self._fd)
            os.fsync(self._fd)
        except OSError as exc:
            raise CrashReportError("private write failed or collided") from exc
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self._fd)
            except FileNotFoundError:
                pass

    def _document(self, name):
        try:
            return _json(_read_at(self._fd, name, MAX_METADATA_BYTES).decode("utf-8"))
        except (OSError, UnicodeError) as exc:
            raise CrashReportError("private metadata read failed") from exc

    def _entries(self):
        entries = []
        for name in sorted(os.listdir(self._fd)):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", name):
                continue
            entry = self._document(name)
            if not isinstance(entry, dict) or set(entry) != {"incident_id", "fingerprint", "report_sha256", "raw_file", "payload"}:
                raise CrashReportError("invalid incident metadata")
            payload = _validate_payload(entry["payload"])
            digest = name[:-5]
            if entry["incident_id"] != digest or entry["report_sha256"] != digest or entry["raw_file"] != digest + ".raw" or payload["report_sha256"] != digest or entry["fingerprint"] != payload["fingerprint"]:
                raise CrashReportError("incident metadata mismatch")
            try:
                raw = _read_at(self._fd, entry["raw_file"], MAX_REPORT_BYTES)
            except OSError as exc:
                raise CrashReportError("incident raw unavailable") from exc
            if hashlib.sha256(raw).hexdigest() != digest:
                raise CrashReportError("incident raw mismatch")
            state_name = self._state_name(entry)
            try:
                state = self._document(state_name)
            except CrashReportError:
                # Missing or corrupt state is uncertain, never an automatic retry.
                state = {"status": "unknown", "issue_url": None, "detail": "publication state unavailable"}
            if not isinstance(state, dict) or set(state) != {"status", "issue_url", "detail"} or not isinstance(state["status"], str) or state["status"] not in {"pending", "sending", "published", "unknown", "rejected"}:
                state = {"status": "unknown", "issue_url": None, "detail": "publication state invalid"}
            if state["status"] == "sending":
                state = dict(state, status="unknown")
            entries.append(dict(entry, publication=state))
        return entries

    def collect(self, raw: bytes, payload: dict) -> dict:
        payload = _validate_payload(payload)
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_REPORT_BYTES:
            raise CrashReportError("invalid raw size")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != payload["report_sha256"]:
            raise CrashReportError("raw payload mismatch")
        for entry in self._entries():
            if entry["incident_id"] == digest:
                if entry["payload"] != payload:
                    previous = dict(entry["payload"])
                    current = dict(payload)
                    previous.pop("fingerprint")
                    current.pop("fingerprint")
                    if previous["schema"] == 1 and current["schema"] == 2:
                        current.pop("diagnostics")
                        current["schema"] = 1
                    if previous != current:
                        raise CrashReportError("incident context collision")
                    # A grouping algorithm upgrade never rewrites immutable raw
                    # incident metadata or forgets its existing publication.

                return entry
        entry = dict(incident_id=digest, fingerprint=payload["fingerprint"], report_sha256=digest, raw_file=digest + ".raw", payload=payload)
        try:
            # Existing orphan raw is never overwritten or silently adopted.
            self._write(digest + ".raw", raw)
            state_name = self._state_name(entry)
            try:
                os.stat(state_name, dir_fd=self._fd, follow_symlinks=False)
            except FileNotFoundError:
                self._write(state_name, json.dumps({"status": "pending", "issue_url": None, "detail": None}).encode())
            encoded = json.dumps(entry, sort_keys=True).encode()
            if len(encoded) > MAX_METADATA_BYTES:
                raise CrashReportError("metadata too large; raw retained")
            self._write(digest + ".json", encoded)
        except OSError as exc:
            raise CrashReportError("incident retention failed") from exc
        return next(item for item in self._entries() if item["incident_id"] == digest)

    def pending(self) -> list[dict]:
        """Pending and uncertain incidents; unknown entries require reconciliation."""
        return [entry for entry in self._entries() if entry["publication"]["status"] in {"pending", "unknown"}]

    @staticmethod
    def _state_name(entry):
        return ("incident-" + entry["incident_id"] if entry["payload"]["schema"] == 2 else entry["fingerprint"]) + ".publication"

    def _publication_entry(self, fingerprint, incident_id):
        entries = [e for e in self._entries() if e["fingerprint"] == fingerprint and (incident_id is None or e["incident_id"] == incident_id)]
        if not entries:
            raise CrashReportError("unknown incident")
        names = {self._state_name(e) for e in entries}
        if len(names) != 1:
            raise CrashReportError("incident_id required for distinct publication states")
        return entries[0]

    def mark_publication(self, fingerprint, status, issue_url=None, detail=None, *, incident_id=None):
        _hash(fingerprint, required=True)
        if not isinstance(status, str) or status not in {"pending", "sending", "published", "unknown", "rejected"}:
            raise CrashReportError("invalid publication status")
        if issue_url is not None and (not isinstance(issue_url, str) or len(issue_url) > 2048):
            raise CrashReportError("invalid private issue URL")
        if detail is not None and (not isinstance(detail, str) or len(detail) > 4096):
            raise CrashReportError("invalid private detail")
        entry = self._publication_entry(fingerprint, incident_id)
        old = entry["publication"]["status"]
        if old == "unknown" and status in {"pending", "sending"}:
            raise CrashReportError("uncertain mutation requires deliberate reconciliation")
        state = dict(status=status, issue_url=issue_url, detail=detail)
        self._write(self._state_name(entry), json.dumps(state).encode(), replace=True)

    def reconcile_publication(self, fingerprint, status, issue_url=None, detail=None, *, incident_id=None):
        """Explicit caller reconciliation after fingerprint search or proven no mutation.

        No network operation is performed. Use only after deliberate resolution of
        an unknown outcome; automatic monitor loops must use mark_publication.
        """
        if status not in ("pending", "published", "rejected"):
            raise CrashReportError("invalid reconciliation status")
        _hash(fingerprint, required=True)
        entry = self._publication_entry(fingerprint, incident_id)
        if issue_url is not None and (not isinstance(issue_url, str) or len(issue_url) > 2048):
            raise CrashReportError("invalid private issue URL")
        if detail is not None and (not isinstance(detail, str) or len(detail) > 4096):
            raise CrashReportError("invalid private detail")
        self._write(self._state_name(entry), json.dumps(dict(status=status, issue_url=issue_url, detail=detail)).encode(), replace=True)
