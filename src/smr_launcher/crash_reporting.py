"""Strict public crash summaries and guarded, explicitly invoked GitHub publication.

This module never reads reports, credentials, game files or local incident storage.
The caller owns durable pending/unknown state and serializes publication by fingerprint.
An unknown mutation must be reconciled by a later deliberate call, never blindly retried.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import shutil
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable

REPOSITORY = "Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon"
OWNER = "Craig-Franklin"
REPOSITORY_ID = 1404818508
PARENT_ID = 950948787
_API = "repos/" + REPOSITORY
_URL = "https://github.com/" + REPOSITORY + "/issues/"
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", re.ASCII)
_VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}", re.ASCII)
_LAUNCHER_VERSION = re.compile(r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:-(?:alpha|beta|rc)\.?[0-9]{1,5})?", re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})", re.ASCII)
_ENUMS = {
    "architecture": {"x86_64", "arm64", "UNKNOWN"},
    "exception_type": {"EXC_BAD_ACCESS", "EXC_BAD_INSTRUCTION", "EXC_ARITHMETIC", "EXC_BREAKPOINT", "EXC_CRASH", "EXC_RESOURCE", "EXC_GUARD", "EXC_CORPSE_NOTIFY", "UNKNOWN"},
    "signal": {"SIGSEGV", "SIGBUS", "SIGILL", "SIGABRT", "SIGFPE", "SIGTRAP", "SIGKILL", "UNKNOWN"},
    "termination_namespace": {"SIGNAL", "CODESIGNING", "WATCHDOG", "RESOURCE", "DYLD", "UNKNOWN"},
}
_FIELDS = {
    "schema", "fingerprint", "report_sha256", "occurred_at", "game_executable_sha256",
    "launcher_version", "game_version", "macos_version", "architecture", "translated",
    "archive_sha256", "variant_id", "scenario_key", "exception_type", "signal",
    "termination_namespace", "exception_codes", "frames",
}
MAX_BODY_CHARACTERS = 60000

_FRAME_FIELDS = {"index", "image_role", "image_uuid", "offset"}


class PublicPayloadError(ValueError):
    """Invalid public data; error messages intentionally contain no supplied values."""


def _require(ok: bool) -> None:
    if not ok:
        raise PublicPayloadError("Invalid public crash payload")


def _matches(value: object, pattern: re.Pattern) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _uint(value: object, maximum: int = 2**64 - 1) -> bool:
    return type(value) is int and 0 <= value <= maximum


def validate_public_payload(payload: dict) -> dict:
    """Return an independent normalized copy, rejecting all unknown or freeform data."""
    _require(type(payload) is dict and type(payload.get("schema")) is int and payload["schema"] in (1, 2))
    _require(set(payload) == (_FIELDS | {"diagnostics"} if payload["schema"] == 2 else _FIELDS))
    for key in ("fingerprint", "report_sha256", "game_executable_sha256"):
        _require(_matches(payload[key], _HASH))
    for key in ("archive_sha256", "variant_id", "scenario_key"):
        _require(payload[key] is None or _matches(payload[key], _HASH))
    _require(_matches(payload["launcher_version"], _LAUNCHER_VERSION))
    for key in ("game_version", "macos_version"):
        _require(type(payload[key]) is str and (payload[key] == "UNKNOWN" or _matches(payload[key], _VERSION)))
    for key, choices in _ENUMS.items():
        _require(type(payload[key]) is str and payload[key] in choices)
    _require(payload["translated"] is None or type(payload["translated"]) is bool)
    _require(_matches(payload["occurred_at"], _TIMESTAMP))
    try:
        stamp = datetime.fromisoformat(payload["occurred_at"].replace("Z", "+00:00"))
        _require(stamp.utcoffset() is not None)
    except (ValueError, OverflowError):
        raise PublicPayloadError("Invalid public crash payload") from None
    codes = payload["exception_codes"]
    _require(type(codes) is list and len(codes) <= 8 and all(_uint(c) for c in codes))
    frames = payload["frames"]
    _require(type(frames) is list and len(frames) <= 20)
    indices = set()
    clean_frames = []
    for frame in frames:
        _require(type(frame) is dict and set(frame) == _FRAME_FIELDS)
        _require(_uint(frame["index"], 2**31 - 1) and frame["index"] not in indices)
        indices.add(frame["index"])
        _require(type(frame["image_role"]) is str and frame["image_role"] in {"game", "system", "other"})
        _require(frame["image_uuid"] is None or _matches(frame["image_uuid"], _UUID))
        _require(frame["offset"] is None or _uint(frame["offset"]))
        clean = dict(frame)
        if clean["image_uuid"] is not None:
            clean["image_uuid"] = clean["image_uuid"].lower()
        clean_frames.append(clean)
    result = dict(payload)
    result["frames"] = clean_frames
    result["exception_codes"] = list(codes)
    if payload["schema"] == 2:
        result["diagnostics"] = _validate_diagnostics(payload["diagnostics"])
    return result


def _validate_diagnostics(value):
    """Independent wire gate: do not import parser/store schemas or extractors."""
    def obj(v, fields):
        _require(type(v) is dict and set(v) == set(fields.split()))
    def count(v):
        _require(_uint(v, 4194304))
    def nullable(v):
        _require(v is None or _uint(v))
    def uid(v):
        _require(v is None or (_matches(v, _UUID) and v == v.lower()))
    def choice(v, options):
        _require(type(v) is str and v in options.split())
    def group(g, cap):
        obj(g, 'source_count retained_count items')
        if g['source_count'] is None:
            _require(value['format'] == 'legacy_crash' and type(g['retained_count']) is int and g['retained_count'] == 0 and g['items'] == [])
            return []
        count(g['source_count'])
        _require(type(g['items']) is list and type(g['retained_count']) is int)
        _require(g['retained_count'] == len(g['items']) <= min(cap, g['source_count']))
        return g['items']
    def indices(items, source):
        last=-1
        for item in items:
            _require(type(item) is dict and type(item.get('index')) is int and last < item['index'] < source)
            last=item['index']
    obj(value, 'version format faulting_thread threads images exception_codes ancillary unavailable omitted')
    _require(type(value['version']) is int and value['version'] == 1)
    choice(value['format'], 'modern_ips legacy_crash')
    threads=group(value['threads'],128); images=group(value['images'],512); codes=group(value['exception_codes'],8)
    _require(all(_uint(c) for c in codes))
    indices(threads,value['threads']['source_count']); indices(images,value['images']['source_count'])
    fault=value['faulting_thread']
    _require(fault is None or (type(fault) is int and 0 <= fault < value['threads']['source_count']))
    for image in images:
        obj(image,'index uuid base size architecture role'); uid(image['uuid'])
        nullable(image['base']); nullable(image['size'])
        choice(image['architecture'],'x86_64 arm64 UNKNOWN'); choice(image['role'],'game system other')
    for thread in threads:
        obj(thread,'index triggered frames registers')
        _require(thread['triggered'] is None or type(thread['triggered']) is bool)
        frames=group(thread['frames'],1024); indices(frames,thread['frames']['source_count'])
        for frame in frames:
            obj(frame,'index image_index offset address')
            ix=frame['image_index']
            _require(ix is None or (type(ix) is int and 0 <= ix < value['images']['source_count']))
            nullable(frame['offset']); nullable(frame['address'])
        registers=thread['registers']; obj(registers,'architecture source_count retained_count values')
        choice(registers['architecture'],'x86_64 rosetta_x86_64 arm64 UNKNOWN')
        x86=set('rax rbx rcx rdx rdi rsi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15 rip rflags cs fs gs trapno err cr2 rosetta_tmp0 rosetta_tmp1 rosetta_tmp2'.split())
        arm={f'x{i}' for i in range(29)} | set('fp lr sp pc cpsr esr far'.split())
        allowed=arm if registers['architecture']=='arm64' else x86 if registers['architecture'] in ('x86_64','rosetta_x86_64') else set()
        _require(type(registers['values']) is dict and set(registers['values']) <= allowed and all(_uint(v) for v in registers['values'].values()))
        count(registers['source_count'])
        _require(type(registers['retained_count']) is int and registers['retained_count'] == len(registers['values']) <= registers['source_count'])
    obj(value['ancillary'],'uptime proc_start_absolute proc_exit_absolute termination_code vm_page_size vm_region_count')
    for n in value['ancillary'].values(): nullable(n)
    required=set('raw_text symbols paths identifiers annotations vm_text memory_contents unknown_fields'.split())
    reasons=required | set('thread_limit frame_limit image_limit invalid_numeric invalid_uuid invalid_reference unknown_registers exception_code_limit'.split())
    unavailable=set('faulting_thread threads registers binary_images numeric_vm exception_codes'.split())
    for name, allowed in (('omitted',reasons),('unavailable',unavailable)):
        entries=value[name]
        _require(type(entries) is list and all(type(v) is str and v in allowed for v in entries) and len(entries)==len(set(entries)))
    _require(required <= set(value['omitted']))
    absent=value['unavailable']
    _require(('faulting_thread' in absent)==(fault is None))
    _require(('threads' in absent)==(not threads))
    _require(('binary_images' in absent)==(not images))
    _require(('registers' in absent)==(not any(t['registers']['values'] for t in threads)))
    _require(('exception_codes' in absent)==(not codes))
    _require(('numeric_vm' in absent)==all(value['ancillary'][n] is None for n in ('vm_page_size','vm_region_count')))
    for reason,g,cap in [('thread_limit',value['threads'],128),('image_limit',value['images'],512),('exception_code_limit',value['exception_codes'],8)]:
        _require((reason in value['omitted'])==(g['source_count'] is not None and g['source_count']>cap))
    _require(('frame_limit' in value['omitted'])==any(t['frames']['source_count']>1024 for t in threads))
    if value['format']=='legacy_crash':
        _require(fault is None and all(g['source_count'] is None for g in (value['threads'],value['images'],value['exception_codes'])) and all(v is None for v in value['ancillary'].values()))
    return json.loads(json.dumps(value))


def _marker(fingerprint: str) -> str:
    return "<!-- smr-crash-fingerprint:" + fingerprint + " -->"


def _compact_diagnostics(d):
    # Fixed column labels preserve every validated value with less repeated text.
    out = dict(d)
    out["image_columns"] = ["index", "uuid", "base", "size", "architecture", "role"]
    out["frame_columns"] = ["index", "image_index", "offset", "address"]
    out["thread_columns"] = ["index", "triggered", "frames_source", "frames_retained", "frames", "registers"]
    out["images"] = dict(d["images"], items=[[i[k] for k in out["image_columns"]] for i in d["images"]["items"]])
    out["threads"] = dict(d["threads"], items=[[
        t["index"], t["triggered"], t["frames"]["source_count"], t["frames"]["retained_count"],
        [[f[k] for k in out["frame_columns"]] for f in t["frames"]["items"]], t["registers"]
    ] for t in d["threads"]["items"]])
    return out


def render_issue(payload: dict) -> tuple[str, str]:
    """Render only independently validated data with fixed prose and stable ordering."""
    p = validate_public_payload(payload)
    title = f"Sid Meier's Railroads! crash: {p['exception_type']} [{p['fingerprint'][:12]}]"
    translated = "unknown" if p["translated"] is None else ("yes" if p["translated"] else "no")
    rows = [
        _marker(p["fingerprint"]), "", "## Crash summary", "",
        f"- Occurred: {p['occurred_at']}",
        f"- Exception: {p['exception_type']}; signal: {p['signal']}; termination: {p['termination_namespace']}",
        "- Exception codes: " + (", ".join(f"0x{c:x}" for c in p["exception_codes"]) or "unknown"),
        f"- Launcher: {p['launcher_version']}; game: {p['game_version']}; macOS: {p['macos_version']}",
        f"- Architecture: {p['architecture']}; translated: {translated}", "", "## Context digests", "",
    ]
    for key in ("fingerprint", "report_sha256", "game_executable_sha256", "archive_sha256", "variant_id", "scenario_key"):
        rows.append(f"- {key}: {p[key] or 'unknown'}")
    rows += ["", "## Stack image offsets", "", "| Frame | Image role | Image UUID | Offset |", "| --- | --- | --- | --- |"]
    for frame in p["frames"]:
        offset = "unknown" if frame["offset"] is None else f"0x{frame['offset']:x}"
        rows.append(f"| {frame['index']} | {frame['image_role']} | {frame['image_uuid'] or 'unknown'} | {offset} |")
    if not p["frames"]:
        rows.append("| unknown | unknown | unknown | unknown |")
    rows += ["", "## Limitations", "", "Automatic sanitized crash summary. Full diagnostics remain private on the reporting computer. No paths, symbols, user notes, logs, saves, assets or report attachments are included. Image offsets require matching build UUIDs for investigation. Unknown metadata is not inferred. A crash report does not establish its cause, reproducibility, map compatibility, feature preservation or save reliability.", "Repeated incidents with this fingerprint are retained locally and grouped into this issue.", ""]
    if p["schema"] == 2:
        rows += ["## Structured diagnostics", "", "Bounded typed projection; source and retained counts identify omissions. Numeric addresses contain no memory contents.", "", "```json", json.dumps(_compact_diagnostics(p["diagnostics"]), sort_keys=True, separators=(",", ":")), "```", ""]
    body = "\n".join(rows)
    if len(body) > MAX_BODY_CHARACTERS:
        raise _BodyTooLarge()
    return title, body


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""


def bounded_command_runner(argv: tuple[str, ...], *, stdin: bytes | None, timeout: float, max_output_bytes: int) -> CommandResult:
    """No shell or credential extraction; time bound and bounded returned stdout.

    Child output uses a temporary file rather than unbounded in-memory capture.
    The timeout/output check is operational, not a strict child disk-write quota.
    Stderr is discarded and is never copied into public outcomes.
    """
    if argv and argv[0] == "gh":
        executable = shutil.which("gh")
        if executable is None:
            executable = next((path for path in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
                               if Path(path).is_file() and os.access(path, os.X_OK)), None)
        if executable is None:
            raise FileNotFoundError("GitHub CLI unavailable")
        argv = (executable, *argv[1:])
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(argv, input=stdin, stdout=output, stderr=subprocess.DEVNULL,
                                timeout=timeout, check=False)
        output.seek(0)
        data = output.read(max_output_bytes + 1)
    if len(data) > max_output_bytes:
        raise ValueError("Command output limit exceeded")
    return CommandResult(result.returncode, data)


class _BodyTooLarge(Exception):
    pass


class _Unavailable(Exception):
    pass


class _Rejected(Exception):
    pass


class GitHubCrashPublisher:
    """One deliberate publication attempt; durable storage belongs to the caller.

    runner(argv, stdin=bytes|None, timeout=seconds, max_output_bytes=count) returns
    CommandResult. checkout=None is installed-bundle mode; a project caller must
    supply its checkout to enable the additional configured-remote guard.
    Calls for the same fingerprint must be serialized by the caller. GitHub has
    no atomic uniqueness constraint for issue bodies, so concurrent publishers
    must not race. An existing URL is a hint and must pass exact readback checks.
    """

    def __init__(self, runner: Callable = bounded_command_runner, *, checkout: Path | None = None):
        self.runner = runner
        self.checkout = checkout

    def _run(self, argv: tuple[str, ...], stdin: bytes | None = None) -> bytes:
        result = self.runner(argv, stdin=stdin, timeout=20.0, max_output_bytes=262144)
        if type(result.returncode) is not int or result.returncode != 0:
            raise _Unavailable()
        if type(result.stdout) is not bytes or len(result.stdout) > 262144:
            raise _Unavailable()
        return result.stdout

    def _api(self, endpoint: str, *, body: dict | None = None) -> object:
        argv = ("gh", "api", "--hostname", "github.com", "--method", "GET" if body is None else "POST", endpoint)
        stdin = None
        if body is not None:
            argv += ("--input", "-")
            stdin = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        raw = self._run(argv, stdin)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise _Unavailable() from None

    def _remote_guard(self) -> None:
        if self.checkout is None:
            return
        prefix = ("git", "-C", str(self.checkout))
        allowed = {"https://github.com/" + REPOSITORY + ".git", "https://github.com/" + REPOSITORY,
                   "git@github.com:" + REPOSITORY + ".git", "ssh://git@github.com/" + REPOSITORY + ".git"}
        # Include every configured push remote, even for branches other than HEAD.
        result = self.runner(prefix + ("config", "--get-regexp", r"^(remote\.pushDefault|branch\..*\.pushRemote)$"),
                             stdin=None, timeout=20.0, max_output_bytes=262144)
        if result.returncode not in (0, 1) or type(result.stdout) is not bytes or len(result.stdout) > 262144:
            raise _Unavailable()
        names = {"origin"}
        for line in result.stdout.decode("utf-8").splitlines():
            parts = line.split()
            if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", parts[1]) or parts[1].startswith("-"):
                raise _Rejected()
            names.add(parts[1])
        for name in sorted(names):
            for flags in (("--all",), ("--push", "--all")):
                urls = self._run(prefix + ("remote", "get-url") + flags + (name,)).decode("utf-8").splitlines()
                if not urls or any(url not in allowed for url in urls):
                    raise _Rejected()

    def _guard(self) -> None:
        account = self._api("user")
        repo = self._api(_API)
        if not isinstance(account, dict) or account.get("login") != OWNER or account.get("type") != "User":
            raise _Rejected()
        if not isinstance(repo, dict):
            raise _Rejected()
        owner, parent = repo.get("owner"), repo.get("parent")
        if (type(repo.get("id")) is not int or repo["id"] != REPOSITORY_ID or repo.get("full_name") != REPOSITORY
                or not isinstance(owner, dict) or owner.get("login") != OWNER or owner.get("type") != "User"
                or not isinstance(parent, dict) or type(parent.get("id")) is not int or parent["id"] != PARENT_ID):
            raise _Rejected()
        self._remote_guard()

    @staticmethod
    def _number(url: str) -> int:
        if type(url) is not str or not re.fullmatch(re.escape(_URL) + r"[1-9][0-9]{0,9}", url):
            raise _Rejected()
        return int(url[len(_URL):])

    def _readback(self, url: str, marker: str, *, require_open=False) -> str:
        number = self._number(url)
        issue = self._api(_API + f"/issues/{number}")
        if (not isinstance(issue, dict) or type(issue.get("number")) is not int or issue["number"] != number
                or issue.get("html_url") != url or "pull_request" in issue
                or type(issue.get("body")) is not str or marker not in issue["body"].splitlines()):
            raise _Rejected()
        if issue.get("state") not in ("open", "closed"):
            raise _Unavailable()
        if require_open and issue["state"] != "open":
            raise _Unavailable()
        return url

    def _find(self, fingerprint: str, *, include_closed=False) -> str | None:
        # Explicit fixed-repository query, including closed issues. Search errors,
        # incomplete results and duplicate markers must never become absence.
        from urllib.parse import urlencode
        query = f'repo:{REPOSITORY} is:issue in:body "smr-crash-fingerprint:{fingerprint}"'
        result = self._api("search/issues?" + urlencode({"q": query, "per_page": 100}))
        if (not isinstance(result, dict) or result.get("incomplete_results") is not False
                or type(result.get("total_count")) is not int or not isinstance(result.get("items"), list)
                or len(result["items"]) != result["total_count"] or result["total_count"] > 100):
            raise _Unavailable()
        marker = _marker(fingerprint)
        matches = []
        for item in result["items"]:
            if not isinstance(item, dict) or type(item.get("body")) is not str:
                raise _Unavailable()
            # Reject foreign result URLs even if a search server returns them.
            self._number(item.get("html_url"))
            if "pull_request" in item:
                raise _Rejected()
            if item.get("state") not in ("open", "closed"):
                raise _Unavailable()
            if marker in item["body"].splitlines() and (include_closed or item["state"] == "open"):
                matches.append(item["html_url"])
        if len(matches) > 1:
            raise _Unavailable()
        return matches[0] if matches else None

    def publish(self, payload: dict, *, existing_issue_url: str | None = None, reconcile_only: bool = False) -> dict:
        """Return published/pending/unknown/rejected with only fixed safe detail codes."""
        mutation_started = False
        try:
            if type(reconcile_only) is not bool:
                raise _Rejected()
            clean = validate_public_payload(payload)
            title, body = render_issue(clean)
            if existing_issue_url is not None:
                self._number(existing_issue_url)
            self._guard()
            if existing_issue_url is not None:
                url = self._readback(existing_issue_url, _marker(clean["fingerprint"]))
                return {"status": "published", "issue_url": url, "detail": "reconciled"}
            url = self._find(clean["fingerprint"], include_closed=reconcile_only)
            if url is not None:
                url = self._readback(url, _marker(clean["fingerprint"]), require_open=not reconcile_only)
                return {"status": "published", "issue_url": url, "detail": "grouped"}
            if reconcile_only:
                return {"status": "unknown", "detail": "reconciliation_no_match"}
            # Fresh exact account/repo/remotes immediately before the only mutation.
            self._guard()
            mutation_started = True
            receipt = self._api(_API + "/issues", body={
                "title": title, "body": body,
                "labels": ["crash:reported", "crash:needs-triage"],
            })
            if not isinstance(receipt, dict):
                raise _Unavailable()
            url = receipt.get("html_url")
            number = self._number(url)
            if type(receipt.get("number")) is not int or receipt["number"] != number or "pull_request" in receipt:
                raise _Rejected()
            self._readback(url, _marker(clean["fingerprint"]))
            return {"status": "published", "issue_url": url, "detail": "created"}
        except _BodyTooLarge:
            return {"status": "unknown" if reconcile_only else "pending", "detail": "body_too_large"}
        except (PublicPayloadError, _Rejected):
            return {"status": "unknown" if mutation_started else "rejected", "detail": "publication_unconfirmed" if mutation_started else "validation_or_target_guard"}
        except Exception:
            # Public boundary: runner/decoder failures never expose supplied data.
            # KeyboardInterrupt/SystemExit remain caller-owned cancellation.
            return {"status": "unknown" if mutation_started or reconcile_only else "pending", "detail": "publication_unconfirmed" if mutation_started or reconcile_only else "service_unavailable"}
