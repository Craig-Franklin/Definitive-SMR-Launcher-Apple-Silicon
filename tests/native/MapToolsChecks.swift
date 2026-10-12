import AppKit
import Foundation

@main struct MapToolsChecks {
    @MainActor static func main() async throws {
        let backend = Backend()
        try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2]])
        let model = LauncherModel(fixture: false, backend: backend)
        model.connected = true
        let map = try MapItem(["id": "synthetic-map", "archive_sha256": String(repeating: "a", count: 64), "variant_id": String(repeating: "b", count: 64)])
        let build = String(repeating: "c", count: 64)
        let tools = NativeMapToolsState()
        let key = NativeMapToolsState.key(map, build)
        precondition(key != NativeMapToolsState.key(map, String(repeating: "d", count: 64)), "Build changes must isolate drafts")
        let other = try MapItem(["id": map.id, "archive_sha256": map.archive, "variant_id": String(repeating: "e", count: 64)])
        precondition(key != NativeMapToolsState.key(other, build), "Edition changes must isolate drafts")
        var draft = MapToolDraft()
        draft.legacyScope = "Retain this legacy draft"
        draft.linkURL = "Retain this link draft"
        draft.issueURL = "Retain this fix attribution"
        draft.kind = "fix_implemented"
        tools.drafts[key] = draft; model.mapToolDraftsPresent = tools.dirty
        precondition(model.dirty, "Native tool drafts must participate in quit protection")
        await tools.load(model, map, build)
        precondition(tools.presentations[key]?.text("scope_text") == "Synthetic scope")
        _ = try await backend.call("fixture.configure", ["reject_inspect": true])
        await tools.load(model, map, build, force: true)
        precondition(tools.presentations[key]?.text("scope_text") == "Synthetic scope", "Read failures retain prior presentation")
        precondition(tools.drafts[key]?.operation == draft.operation && tools.errors[key] == "Synthetic ledger unavailable")
        _ = try await backend.call("fixture.configure", ["reject": true])
        await tools.perform(model, map, build, "record_fix", ["operation_id": draft.operation])
        precondition(tools.drafts[key]?.legacyScope == draft.legacyScope && tools.drafts[key]?.issueURL == draft.issueURL)
        precondition(tools.errors[key] == "Synthetic validation rejection" && !tools.unknown.contains(key))
        _ = try await backend.call("fixture.configure")
        await tools.perform(model, map, build, "record_fix", ["operation_id": draft.operation])
        precondition(tools.drafts[key]?.issueURL == "" && tools.drafts[key]?.legacyScope == draft.legacyScope && tools.drafts[key]?.linkURL == draft.linkURL, "Recording a fix must preserve unrelated drafts")
        let counts = try await backend.call("fixture.counts")
        precondition(counts["maptools.record_fix"] as? Int == 2 && counts["snapshot"] == nil, "No automatic retries or full-library reset")
        _ = try await backend.call("fixture.configure", ["reject": true, "error": "Map tool outcome needs inspection; do not repeat the action: synthetic full disk"])
        await tools.perform(model, map, build, "legacy_record")
        precondition(tools.unknown.contains(key) && tools.drafts[key]?.legacyScope == draft.legacyScope)
        await tools.perform(model, map, build, "legacy_record")
        let heldCounts = try await backend.call("fixture.counts")
        precondition(heldCounts["maptools.legacy_record"] as? Int == 1, "Unknown storage outcome cannot be repeated")
        tools.unknown.remove(key) // Test-only new scenario, not a product reset action.
        _ = try await backend.call("fixture.configure", ["disconnect": true])
        await tools.perform(model, map, build, "legacy_record")
        precondition(!model.connected && tools.unknown.contains(key) && tools.drafts[key]?.legacyScope == draft.legacyScope)
        await tools.perform(model, map, build, "legacy_record")
        precondition(tools.unknown.contains(key), "Disconnect cannot trigger mutation retry")
        print("Native map-tool checks passed: exact draft keys, retained read/error drafts, partial completion, quit protection, no gallery reset, storage/disconnect unknown holds")
    }
}
