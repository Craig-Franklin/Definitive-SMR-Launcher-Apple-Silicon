import AppKit
import Foundation

@main struct ReportingChecks {
    @MainActor static func main() async throws {
        func require(_ value: Bool, _ message: String, line: UInt = #line) { precondition(value, message + " at line \(line)") }
        func opened(_ mode: String) async throws -> (LauncherModel, Backend, MapItem) {
            let backend = Backend()
            try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2], "--case", mode])
            let model = LauncherModel(fixture: false, backend: backend)
            model.connected = true; await model.refresh()
            let map = model.maps[0]; model.selection = map.id; model.record(map)
            return (model, backend, map)
        }
        let (model, backend, map) = try await opened("common")
        let info = try await backend.call("fixture.info")
        require(model.outcome(map).scenario == "Synthetic Coast.xml" && model.outcome(map).game == info.text("game"), "Known exact scope reused visibly")
        let session = model.outcome(map).attempt
        model.chooseOutcome(map, "Played"); await model.confirm(map)
        require(model.error.isEmpty && model.outcome(map).outcome.isEmpty && model.outcome(map).attempt == session, "Keep context after success")
        model.chooseOutcome(map, "Played"); await model.confirm(map)
        let once = try await backend.call("fixture.counts")
        require(once["history.record"] as? Int == 1 && model.reportAlreadyRecorded(map, model.outcome(map)), "Reuse confirmed observations without another event")
        model.chooseOutcome(map, "Saved"); await model.confirm(map)
        require(model.error.contains("Choose the exact save") && model.outcome(map).outcome == "Saved", "Validation retains draft")
        let staleScenarioChoice = model.beginOutcomeSaveChoice(map)
        let delayedScenario = Task {
            try await Task.sleep(nanoseconds: 30_000_000)
            model.acceptOutcomeSaveChoice(map, staleScenarioChoice, info.text("save"))
        }
        model.setOutcomeScenario(map, "Synthetic Highlands.xml")
        model.setOutcomeScenario(map, "Synthetic Coast.xml")
        try await delayedScenario.value
        require(model.outcome(map).save.isEmpty, "Delayed digest cannot survive a scenario change and switch back")
        let olderSaveChoice = model.beginOutcomeSaveChoice(map)
        let newerSaveChoice = model.beginOutcomeSaveChoice(map)
        let delayedOldChoice = Task {
            try await Task.sleep(nanoseconds: 30_000_000)
            model.acceptOutcomeSaveChoice(map, olderSaveChoice, info.text("save"))
        }
        model.acceptOutcomeSaveChoice(map, newerSaveChoice, String(repeating: "e", count: 64))
        try await delayedOldChoice.value
        require(model.outcome(map).save == String(repeating: "e", count: 64), "Newer exact-save choice wins late old digest")
        let staleOutcomeChoice = model.beginOutcomeSaveChoice(map)
        model.chooseOutcome(map, "Resumed"); model.chooseOutcome(map, "Saved")
        model.acceptOutcomeSaveChoice(map, staleOutcomeChoice, info.text("save"))
        require(model.outcome(map).save == String(repeating: "e", count: 64), "Outcome changes invalidate pending digest consent")
        let staleSessionChoice = model.beginOutcomeSaveChoice(map)
        model.record(map, attempt: "other-session"); model.record(map, attempt: session)
        model.acceptOutcomeSaveChoice(map, staleSessionChoice, info.text("save"))
        require(model.outcome(map).save == String(repeating: "e", count: 64), "Session switch-back cannot revive old digest choice")
        let staleSelectionChoice = model.beginOutcomeSaveChoice(map)
        model.selection = model.maps[1].id; model.selection = map.id
        model.acceptOutcomeSaveChoice(map, staleSelectionChoice, info.text("save"))
        require(model.outcome(map).save == String(repeating: "e", count: 64), "Map switch-back cannot revive old digest choice")
        model.setOutcomeSave(map, info.text("save"))
        await model.confirm(map)
        require(model.error.isEmpty && model.outcome(map).save == info.text("save") && model.outcome(map).game == info.text("game"), "Save identity and game context retained")
        model.chooseOutcome(map, "Resumed"); model.updateOutcome(map) { $0.continued = true }; await model.confirm(map)
        let rows = model.reportRows(map)
        require(rows.filter { $0.text("kind") == "save_reopened" }.count == 1 && rows.filter { $0.text("kind") == "continued_play" }.count == 1, "Explicit reopen/continuation batch preserved")
        require(rows.filter { $0.text("kind") == "unknown_exit" }.count == 1, "Unknown observation remains")
        model.updateOutcome(map) { $0.outcome = "Played"; $0.continued = true }
        require(model.reportAlreadyRecorded(map, model.outcome(map)), "Hidden continuation flag cannot bypass Played duplicate detection")
        model.chooseOutcome(map, "Played")
        require(!model.outcome(map).continued, "Changing away from Resumed clears the hidden human assertion")
        let after = try await backend.call("fixture.counts")
        require(after["history.record"] as? Int == 3 && after["snapshot"] as? Int == 1, "Three explicit submissions, no library reset")
        model.chooseOutcome(map, "retired")
        require(model.outcome(map).outcome == "Completed" && model.outcome(map).reason == "retired", "Completion reason is an explicit outcome choice")
        let oldKey = model.outcomeKey(map), oldDraft = model.outcome(map)
        model.record(map, attempt: "another-session")
        model.updateOutcome(map) { $0.outcome = "Problem" }
        model.record(map, attempt: session)
        require(model.outcome(map) == oldDraft, "Entire session draft survives deferral/switching")
        let other = model.maps[1]; await model.loadDetails(other.id); model.record(other)
        require(model.outcome(other).outcome.isEmpty && model.outcomes[oldKey] == oldDraft, "Other map draft remains separate")
        let changed = try MapItem(["id": map.id, "archive_sha256": map.archive, "variant_id": String(repeating: "d", count: 64), "game_executable_sha256": map.build])
        model.maps[0] = changed; model.record(changed)
        require(model.outcome(changed).outcome.isEmpty && model.outcomes[oldKey] == oldDraft, "Edition identity separates draft")
        await model.confirm(map)
        require(model.error.contains("target or build changed"), "Old edition cannot submit")
        model.maps[0] = map; model.record(map, attempt: session)
        model.details[map.id] = ["context": ["game_executable_sha256": "different-build"]]
        await model.confirm(map)
        require(model.error.contains("target or build changed") && model.outcomes[oldKey] == oldDraft, "Build change retains old draft without mutation")

        let (ambiguous, _, ambiguousMap) = try await opened("ambiguous")
        require(ambiguous.outcome(ambiguousMap).game.isEmpty && ambiguous.reportGames(ambiguousMap, ambiguous.outcome(ambiguousMap)).count == 2, "Ambiguous game requires explicit choice")
        ambiguous.chooseOutcome(ambiguousMap, "Played"); await ambiguous.confirm(ambiguousMap)
        require(ambiguous.error.contains("Several games") && ambiguous.outcome(ambiguousMap).outcome == "Played", "Backend still refuses ambiguity")
        let (missing, _, missingMap) = try await opened("missing-scenario")
        require(missing.outcome(missingMap).scenario.isEmpty, "Metadata scenarios are never an observed binding")
        missing.chooseOutcome(missingMap, "Played"); await missing.confirm(missingMap)
        require(missing.error.contains("Choose the scenario"), "Unknown scenario cannot certify play")
        let (reject, rejectBackend, rejectMap) = try await opened("reject")
        reject.chooseOutcome(rejectMap, "Played"); let before = reject.outcome(rejectMap)
        await reject.confirm(rejectMap)
        require(reject.outcome(rejectMap) == before && reject.outcomeUnknown.isEmpty, "Prewrite validation rejection is recoverable")
        await reject.confirm(rejectMap)
        let retry = try await rejectBackend.call("fixture.counts")
        require(reject.error.isEmpty && retry["history.record"] as? Int == 2, "Only explicit corrected resubmission retries validation")
        let (unknown, unknownBackend, unknownMap) = try await opened("unknown")
        unknown.chooseOutcome(unknownMap, "Played"); let held = unknown.outcome(unknownMap)
        _ = try await unknownBackend.call("fixture.configure", ["delay_record": true])
        let firstUnknown = Task { await unknown.confirm(unknownMap) }
        try await Task.sleep(nanoseconds: 30_000_000)
        let queuedUnknown = Task { await unknown.confirm(unknownMap) }
        await firstUnknown.value; await queuedUnknown.value
        await unknown.confirm(unknownMap)
        let heldCounts = try await unknownBackend.call("fixture.counts")
        require(unknown.error.hasPrefix("History outcome needs inspection") && unknown.outcomeUnknown.contains(unknown.outcomeKey(unknownMap)), "Partial append is UNKNOWN")
        require(unknown.outcome(unknownMap) == held && heldCounts["history.record"] as? Int == 1, "UNKNOWN preserves draft and never retries")
        let (problem, problemBackend, problemMap) = try await opened("problem")
        problem.chooseOutcome(problemMap, "Problem")
        require(!problem.outcome(problemMap).incident.isEmpty, "Unique retained incident reused visibly")
        await problem.confirm(problemMap)
        problem.chooseOutcome(problemMap, "Problem")
        require(problem.reportAlreadyRecorded(problemMap, problem.outcome(problemMap)), "Identified failure attribution cannot silently duplicate")
        problem.updateOutcome(problemMap) { $0.incident = "" }
        require(!problem.reportAlreadyRecorded(problemMap, problem.outcome(problemMap)), "An explicitly new failure remains a distinct assertion")
        await problem.confirm(problemMap)
        require(Set(problem.reportRows(problemMap).filter { $0.text("kind") == "crash_unknown" }.map { $0.text("incident_id") }).count == 2, "Distinct new failure is preserved")
        _ = try await problemBackend.call("fixture.configure", ["delay_record": true])
        problem.chooseOutcome(problemMap, "Played")
        let inFlight = Task { await problem.confirm(problemMap) }
        try await Task.sleep(nanoseconds: 30_000_000)
        problem.updateOutcome(problemMap) { $0.outcome = "Completed"; $0.reason = "retired" }
        problem.selection = problem.maps[1].id; problem.status = "Other map status"
        await inFlight.value
        require(problem.outcome(problemMap).outcome == "Completed" && problem.outcome(problemMap).reason == "retired", "A late success cannot erase newer edits")
        require(problem.status == "Other map status", "A late success cannot overwrite another map status")
        _ = try await problemBackend.call("fixture.configure")
        problem.busy = true
        let queued = Task { await problem.confirm(problemMap) }
        try await Task.sleep(nanoseconds: 30_000_000)
        problem.updateOutcome(problemMap) { $0.reason = "goals_met" }
        problem.busy = false
        await queued.value
        require(problem.error.contains("target or build changed"), "Changed queued assertion cannot inherit consent")
        print("Reporting checks passed: exact scoped drafts/context, complete three-submission/four-checkpoint flow, duplicate suppression, ambiguity, missing scenario, validation and partial-write UNKNOWN")
    }
}
