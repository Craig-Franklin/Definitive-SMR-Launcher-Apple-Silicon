"""macOS system voices and asynchronous speech; no third-party voice downloads."""
from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
import threading
from typing import Optional


class VoiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Voice:
    identifier: str
    language: str
    sample: str = ""

    @property
    def name(self) -> str:
        return self.identifier


_VOICE = re.compile(r"^(.+?)\s+([a-z]{2,3}_[A-Za-z0-9_]+)\s+#\s?(.*)$")


def parse_voices(output: str) -> tuple[Voice, ...]:
    """Keep the full say identifier, including language suffixes and spaces."""
    result = {}
    for line in output.splitlines():
        match = _VOICE.match(line)
        if match:
            identifier, language, sample = match.groups()
            result[identifier] = Voice(identifier, language.replace("_", "-"), sample)
    return tuple(sorted(result.values(), key=lambda voice: (voice.language, voice.identifier.casefold())))


def list_voices() -> tuple[Voice, ...]:
    try:
        result = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True,
                                text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceError("Could not list macOS voices. Open Read & Speak in System Settings.") from exc
    voices = parse_voices(result.stdout)
    if not voices:
        raise VoiceError("No macOS voices were found. Open Read & Speak settings to add a voice.")
    return voices


def voices_for_language(voices: tuple[Voice, ...], language: str) -> tuple[Voice, ...]:
    base = language.split("-")[0]
    return tuple(voice for voice in voices if voice.language.split("-")[0] == base)


class SpeechHandle:
    """Own one speech process; writing the briefing never blocks Tk's UI thread."""
    def __init__(self, process: subprocess.Popen, text: str):
        self.process = process
        self.error: Optional[str] = None
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._feed, args=(text,), daemon=True)
        self._thread.start()

    def _feed(self, text: str) -> None:
        try:
            _, error = self.process.communicate(text)
            if self.process.returncode and not self._stopped.is_set():
                self.error = error.strip() or "macOS speech stopped unexpectedly."
        except (OSError, ValueError) as exc:
            if not self._stopped.is_set():
                self.error = str(exc)

    def stop(self) -> None:
        self._stopped.set()
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass

    def poll(self):
        return self.process.poll()

    def terminate(self) -> None:
        self.stop()

    @property
    def done(self) -> bool:
        """True after the worker has captured any native speech error."""
        return not self._thread.is_alive()

    @property
    def running(self) -> bool:
        return self.process.poll() is None


def speak(text: str, voice_id: str = "", *, available_voices: Optional[tuple[Voice, ...]] = None) -> SpeechHandle:
    if not text.strip():
        raise VoiceError("There is no briefing to read.")
    command = ["/usr/bin/say"]
    if voice_id:
        if not isinstance(voice_id, str) or len(voice_id) > 256 or any(ord(char) < 32 for char in voice_id):
            raise VoiceError("Invalid macOS voice identifier.")
        # GUI callers may supply a cached list. Never enumerate voices on the UI thread.
        # Without a cache, say validates the identifier and SpeechHandle captures its error.
        if available_voices is not None and voice_id not in {voice.identifier for voice in available_voices}:
            raise VoiceError("The selected voice is not installed. Choose another voice or open Read & Speak settings.")
        command += ["-v", voice_id]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        raise VoiceError("Could not start macOS speech.") from exc
    return SpeechHandle(process, text)


def open_voice_settings() -> None:
    """Ask macOS to show its own voice manager; fallback opens System Settings."""
    try:
        result = subprocess.run(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.universalaccess?SpokenContent"],
                                capture_output=True, timeout=15)
        if result.returncode:
            subprocess.run(["/usr/bin/open", "-a", "System Settings"], check=True,
                           capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceError("Open System Settings → Accessibility → Read & Speak → Manage Voices.") from exc
