import AppKit
import Foundation

@main struct ToolsChecks {
    @MainActor static func main() async throws {
        let backend = Backend()
        try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2]])
        let model = LauncherModel(fixture: false, backend: backend)
        model.connected = true
        model.personal["synthetic"] = PersonalDraft(title: "Draft", notes: "Retain me", dirty: true)
        model.remoteSelection = ["A.7z"]
        model.busy = true
        let value = await model.toolControl("tools.status")
        precondition(value != nil && model.busy && model.dirty)
        let refused = await model.toolControl("tools.start")
        precondition(refused == nil && model.busy)
        model.busy = false
        model.tools = ["update_sha256": String(repeating: "a", count: 64), "update_version": "99.0.0", "update_size": 5]
        let consent = model.captureToolConsent("update.stage")
        model.tools["update_sha256"] = String(repeating: "b", count: 64)
        await model.submitToolConsent(consent)
        precondition(model.error.contains("changed") && model.dirty)
        let before = try await backend.call("fixture.counts")
        precondition(before["tools.start"] == nil)
        await model.runTool("collection.import", ["consent": true])
        try await Task.sleep(nanoseconds: 100_000_000)
        precondition(model.toolJob.text("state") == "unknown")
        precondition(model.error.contains("inspection") && model.dirty)
        precondition(model.remoteSelection == ["A.7z"])
        let after = try await backend.call("fixture.counts")
        precondition(after["tools.start"] as? Int == 1)
        await model.runTool("disconnect")
        precondition(!model.connected && model.error.contains("unfinished") && model.dirty)
        print("Native tools consent/control/unknown-result checks passed; synthetic pipe only")
    }
}
