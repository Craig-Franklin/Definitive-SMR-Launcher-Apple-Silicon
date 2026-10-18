"""Compile the production metadata projection; no window API, capture or GUI calls."""

from pathlib import Path
import platform
import subprocess
from tempfile import TemporaryDirectory
import unittest


@unittest.skipUnless(platform.system() == "Darwin", "CoreGraphics targets macOS")
class WindowMetadataTests(unittest.TestCase):
    def test_pid_filter_serialization_unknown_fields_and_error_results(self):
        source = Path(__file__).resolve().parents[1] / (
            "scripts/research/native_window_capture/WindowProbe.swift"
        )
        # Compile the exact production declarations. Exclude all CLI branches,
        # including native enumeration and the visible fixture.
        declarations = source.read_text().split("let args = CommandLine.arguments\n", 1)[0]
        checks = r"""
let pid: Int32 = 42
let privateRow: [String: Any] = [kCGWindowOwnerPID as String: 84,
    kCGWindowName as String: "FOREIGN_PRIVATE_TITLE", "unexpected": Date()]
let owned: [String: Any] = [kCGWindowOwnerPID as String: pid,
    kCGWindowNumber as String: 7, kCGWindowLayer as String: 3,
    kCGWindowIsOnscreen as String: false, kCGWindowAlpha as String: 0.0,
    kCGWindowSharingState as String: 0,
    kCGWindowBounds as String: ["X": -5, "Y": 0, "Width": 0, "Height": 10],
    kCGWindowName as String: "OWNED_PRIVATE_TITLE", kCGWindowOwnerName as String: "PRIVATE_APP"]
func result(_ rows: [[String: Any]]?, _ access: Bool = true) -> [String: Any] {
    pidWindowMetadata(rows, pid: pid, access: access, started: 10, finished: 11)
}
let full = result([privateRow, owned])
let rows = full["windows"] as! [[String: Any]]
precondition(rows.count == 1 && rows[0]["pid"] as! Int32 == pid)
precondition(Set(rows[0].keys) == ["window", "pid", "bounds", "layer", "on_screen", "alpha", "sharing"])
precondition(rows[0]["layer"] as! Int == 3 && rows[0]["on_screen"] as! Bool == false)
precondition(rows[0]["alpha"] as! Double == 0 && rows[0]["sharing"] as! Int == 0)
precondition((rows[0]["bounds"] as! [String: Any])["width"] as! Int == 0)
let encoded = String(data: try! JSONSerialization.data(withJSONObject: full), encoding: .utf8)!
precondition(!encoded.contains("PRIVATE") && !encoded.contains("unexpected") && !encoded.contains("84"))
precondition(full["started_unix_seconds"] as! Double == 10 && full["finished_unix_seconds"] as! Double == 11)

let missing = result([[kCGWindowOwnerPID as String: pid]])["windows"] as! [[String: Any]]
precondition(missing.count == 1)
for key in ["window", "bounds", "layer", "on_screen", "alpha", "sharing"] {
    precondition(missing[0][key] is NSNull)
}
var malformed = owned
malformed[kCGWindowAlpha as String] = Double.nan
malformed[kCGWindowNumber as String] = 1.5
malformed[kCGWindowLayer as String] = true
malformed[kCGWindowIsOnscreen as String] = 1
let invalid = result([malformed])["windows"] as! [[String: Any]]
for key in ["alpha", "window", "layer", "on_screen"] { precondition(invalid[0][key] is NSNull) }
_ = try! JSONSerialization.data(withJSONObject: result([malformed]))
let booleanOwner = pidWindowMetadata([[kCGWindowOwnerPID as String: true]], pid: 1,
    access: true, started: 10, finished: 11)["windows"] as! [[String: Any]]
precondition(booleanOwner.isEmpty)

let empty = result([])
precondition(empty["status"] as! String == "ok" && (empty["windows"] as! [[String: Any]]).isEmpty)
let limited = result([], false)
precondition(limited["status"] as! String == "permission_limited")
precondition(limited["screen_recording_authorized"] as! Bool == false)
let unavailable = result(nil)
precondition(unavailable["status"] as! String == "enumeration_error" && unavailable["windows"] is NSNull)
_ = try! JSONSerialization.data(withJSONObject: unavailable)
print("Metadata checks passed: exact PID/private fields, no visibility filters, unknowns, permission/error/empty distinction")
"""
        with TemporaryDirectory(prefix="smr-window-metadata-check-") as directory:
            main = Path(directory) / "main.swift"
            executable = Path(directory) / "WindowMetadataChecks"
            main.write_text(declarations + checks)
            subprocess.run(
                ["xcrun", "swiftc", "-swift-version", "5", str(main), "-o", str(executable)],
                check=True,
                timeout=60,
            )
            subprocess.run([str(executable)], check=True, timeout=10)
