import AppKit
import Foundation

@main struct ModelChecks {
    @MainActor static func main() async throws {
        let model = LauncherModel(fixture: true)
        model.maps = try (0..<1000).map { index in
            try MapItem(["id": "fixture-\(index)", "name": String(format: "Map %04d", index),
                "archive_sha256": String(repeating: "a", count: 64), "variant_id": String(repeating: "b", count: 64),
                "metadata": ["author": index % 2 == 0 ? "Cedar" : "Coast", "map_type": "Single player"],
                "verification": "Not verified", "summary": ["light": index == 0 ? "amber" : "grey"]])
        }
        let chosen = model.maps[25]
        model.select(chosen)
        model.query = "Cedar"
        precondition(model.visible.count == 500)
        precondition(model.selection == chosen.id, "Search must retain hidden selection")
        model.query = ""; model.filter = "Known issue"
        precondition(model.visible.map(\.id) == ["fixture-0"], "Unknowns are not known issues")
        model.filter = "All maps"; model.sort = "Name Z–A"
        precondition(model.visible.first?.id == "fixture-999")
        model.edit(chosen)
        model.personal[chosen.id]?.notes = "Retained synthetic draft"
        model.personal[chosen.id]?.dirty = true
        model.destination = "Preferences"; model.destination = "Map Library"
        model.select(model.maps[30]); model.select(chosen)
        precondition(model.personal[chosen.id]?.notes == "Retained synthetic draft")
        precondition(model.dirty)
        await model.loadDetails(chosen.id)
        model.record(chosen)
        precondition(model.outcomes[chosen.id]?.attempt == "fictional-attempt")
        model.outcomes[chosen.id]?.outcome = "Saved"
        await model.confirm(chosen)
        precondition(model.error.contains("Choose the exact save"))
        precondition(model.outcomes[chosen.id] != nil)
        let legacyIssue = try MapItem(["id": "legacy", "archive_sha256": "a", "variant_id": "b", "verification": "Known issue", "summary": ["label": "Never opened"]])
        precondition(legacyIssue.status == "Known issue · Never opened")
        await model.savePersonal(chosen)
        precondition(model.personal[chosen.id] != nil, "Fixture must never report a persisted save")
        var elapsed: [Double] = []
        for index in 0..<40 {
            let start = ContinuousClock.now
            model.query = index % 2 == 0 ? "Cedar" : "Map"
            model.sort = index % 2 == 0 ? "Name A–Z" : "Name Z–A"
            _ = model.visible
            let duration = start.duration(to: .now).components
            elapsed.append(Double(duration.seconds) * 1000 + Double(duration.attoseconds) / 1e15)
        }
        let p95 = elapsed.sorted()[37]
        precondition(p95 < 250, "In-memory filtering/sorting regressed")
        print(String(format: "Native model checks passed; 1000 synthetic maps, 40 in-memory queries, p95 %.2f ms (not render timing)", p95))

        let backend = Backend()
        var eventSeen = false
        backend.event = { name, data in eventSeen = name == "fixture" && data.flag("synthetic") }
        try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2]])
        let reply = try await backend.call("echo", ["text": "Unicode café · 地图"])
        precondition(reply.text("text") == "Unicode café · 地图" && eventSeen)
        do { _ = try await backend.call("reject"); preconditionFailure("Service failure was swallowed") }
        catch { precondition(error.localizedDescription == "synthetic rejection") }
        do { _ = try await backend.call("disconnect"); preconditionFailure("Lost mutation result was swallowed") }
        catch { precondition(error.localizedDescription.contains("unknown") || error.localizedDescription.contains("inspect")) }
        print("Native pipe checks passed; partial replies, events, explicit rejection and unknown disconnect")
    }
}
