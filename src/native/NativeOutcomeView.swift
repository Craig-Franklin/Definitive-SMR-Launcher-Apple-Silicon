import AppKit
import SwiftUI

/// Contextual assertions only. Python remains the evidence and identity authority.
struct NativeOutcomeView: View {
    @ObservedObject var model: LauncherModel
    let map: MapItem
    private var draft: OutcomeDraft { model.outcome(map) }
    private var rows: [[String: Any]] { model.reportRows(map) }
    private var opens: [[String: Any]] { unique(rows.filter { ["process_confirmed", "launch_attempted"].contains($0.text("kind")) }, "attempt_id") }
    private var games: [String] { model.reportGames(map, draft) }
    private var held: Bool { model.working || model.outcomeUnknown.contains(model.outcomeKey(map)) }
    private var failures: [[String: Any]] {
        unique(rows.filter {
            ["crash_load", "crash_play", "crash_unknown", "resource_stop"].contains($0.text("kind")) &&
            $0.text("attempt_id") == draft.attempt && ($0.text("scenario").isEmpty || $0.text("scenario") == draft.scenario)
        }, "incident_id")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("What happened in this session?").font(.headline)
            Text("Record only what you personally confirmed. Process exit leaves gameplay unknown.").foregroundStyle(.secondary)
            if model.outcomeUnknown.contains(model.outcomeKey(map)) {
                Text("The last confirmation outcome is unknown. This draft is retained. Inspect history before another submission.").foregroundStyle(.orange)
            }
            if opens.isEmpty {
                Text("No recorded launch session for this edition/build. An exact report needs an existing attempt or confirmed process.")
                Button("Refresh History") { Task { await model.loadDetails(map.id, force: true); model.record(map) } }
                Button("Record Legacy Mac Test…") { model.section = "Legacy Mac Test" }
            } else {
                Text("Session \(draft.attempt.prefix(12)) · \(draft.scenario.isEmpty ? "Scenario unknown" : draft.scenario)").textSelection(.enabled)
                if !draft.game.isEmpty && draft.game != "new" { Text("Reusing recorded game \(draft.game.prefix(12)). No new game assertion is inferred.").font(.caption).foregroundStyle(.secondary) }
                DisclosureGroup("Inspect or Change Session Context") { context.padding(.top, 8) }
                Picker("Outcome", selection: Binding(get: { draft.outcome == "Completed" && !draft.reason.isEmpty ? draft.reason : draft.outcome }, set: { model.chooseOutcome(map, $0) })) {
                    Text("Choose outcome").tag("")
                    ForEach(["Played", "Problem", "Saved", "Resumed"], id: \.self) { Text($0) }
                    Text("Completed — met all scenario goals").tag("goals_met")
                    Text("Completed — retired").tag("retired")
                }
                if !draft.outcome.isEmpty {
                    if draft.scenario.isEmpty && (draft.outcome != "Problem" || ["crash_load", "crash_play"].contains(draft.problem)) { scenario }
                    if draft.outcome != "Problem" && (games.count > 1 || (draft.outcome == "Completed" && draft.game.isEmpty)) { game }
                    if draft.outcome == "Problem" { problem }
                    if ["Saved", "Resumed"].contains(draft.outcome) { save }
                    if draft.outcome == "Completed" && draft.reason.isEmpty {
                        Picker("Completion reason", selection: binding(\.reason)) {
                            Text("Choose reason").tag(""); Text("Met all scenario goals").tag("goals_met"); Text("Retired").tag("retired")
                        }
                    }
                    Text(assertion).font(.callout)
                    if model.reportAlreadyRecorded(map, draft) {
                        Text("This assertion is already recorded. Reusing it does not create another event. Choose a different outcome to add a new fact.").foregroundStyle(.secondary)
                    }
                }
                if !rows.isEmpty {
                    DisclosureGroup("Recorded Observations — Retained") {
                        ForEach(Array(rows.filter { $0.text("attempt_id") == draft.attempt }.enumerated()), id: \.offset) { _, row in
                            Text("\(row.text("label", row.text("kind"))) · \(row.text("source")) · \(row.text("scenario", "Scenario unknown"))")
                                .font(.caption).textSelection(.enabled)
                        }
                    }
                }
                HStack {
                    Button("Keep Draft & Close") { model.section = "Overview" }
                    Spacer()
                    Button("Record Confirmation") { Task { await model.confirm(map) } }
                        .buttonStyle(.borderedProminent).disabled(draft.outcome.isEmpty || model.reportAlreadyRecorded(map, draft))
                }
                Text("After recording, this session's context stays here for another outcome. Each human assertion still needs your confirmation. Legacy Mac tests remain separate.").font(.caption).foregroundStyle(.secondary)
            }
        }.disabled(held)
    }
    private var context: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(map.name).font(.headline)
            Text("Archive: \(map.archive)\nEdition: \(map.variant)\nBuild: \(model.reportBuild(map))\nSession: \(draft.attempt)").font(.caption.monospaced()).textSelection(.enabled)
            Picker("Launch session", selection: Binding(get: { draft.attempt }, set: { model.record(map, attempt: $0) })) {
                ForEach(opens, id: \.sessionID) { row in Text("\(row.text("at")) · \(row.text("attempt_id").prefix(12))").tag(row.text("attempt_id")) }
            }
            scenario
            game
        }
    }
    private var scenario: some View {
        Picker("Scenario", selection: Binding(get: { draft.scenario }, set: { model.setOutcomeScenario(map, $0) })) {
            Text("Unknown / choose scenario").tag("")
            ForEach(model.details[map.id]?["scenarios"] as? [String] ?? [], id: \.self) { Text($0) }
        }
    }
    private var game: some View {
        Picker("Game", selection: binding(\.game)) {
            Text(games.count > 1 ? "Choose the exact bound game" : "No recorded game binding").tag("")
            Text("I started a new game").tag("new")
            ForEach(model.reportAvailableGames(map, draft), id: \.self) { Text("Recorded game \($0.prefix(12))").tag($0) }
        }
    }
    private var problem: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("What happened?", selection: binding(\.problem)) {
                Text("Crash · phase unknown").tag("crash_unknown"); Text("Crashed during load").tag("crash_load")
                Text("Crashed during play").tag("crash_play"); Text("Resource stop").tag("resource_stop")
            }
            if !draft.incident.isEmpty { Text("Reusing retained incident \(draft.incident.prefix(12)); its occurrence and provenance stay intact.").font(.caption) }
            DisclosureGroup("Attribute an Incident or Failed Checkpoint") {
                Picker("Incident", selection: binding(\.incident)) {
                    Text("New player-reported failure").tag("")
                    ForEach(failures, id: \.incidentID) { row in Text("\(row.text("at")) · \(row.text("label", row.text("kind")))").tag(row.text("incident_id")) }
                }
                Picker("Failed checkpoint", selection: binding(\.checkpoint)) {
                    Text("Unknown").tag(""); Text("Map loaded").tag("map_loaded"); Text("Playable").tag("playable")
                    Text("Save made").tag("save_made"); Text("Exact save reopened").tag("save_reopened"); Text("Continued play").tag("continued_play")
                }
            }
        }
    }
    private var save: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Exact save SHA-256", selection: binding(\.save)) {
                Text("Choose exact save").tag("")
                ForEach(Array(Set(rows.filter { $0.text("scenario") == draft.scenario }.map { $0.text("save_id") }.filter { !$0.isEmpty } + (draft.save.isEmpty ? [] : [draft.save]))).sorted(), id: \.self) { Text($0).tag($0) }
            }
            Button("Choose Exact Save…") { chooseSave() }
            Text("Read-only identity check; only its digest is recorded. Existence does not prove saving or reopening.").font(.caption).foregroundStyle(.secondary)
            if draft.outcome == "Resumed" {
                Toggle("I also continued playing after reopening this exact save", isOn: Binding(get: { draft.continued }, set: { value in model.updateOutcome(map) { $0.continued = value } }))
            }
        }
    }
    private var assertion: String {
        switch draft.outcome {
        case "Played": return "Confirm only if track, stations and trains worked. This does not confirm completion, saving or reopening."
        case "Problem": return "Confirm the actual failure or its attribution. Earlier failures remain visible."
        case "Saved": return "Confirm you made this exact save. A digest alone does not prove a successful save."
        case "Resumed": return "Confirm you reopened this exact save. Continued play requires the separate checked assertion."
        case "Completed": return "Confirm the chosen completion reason. This does not clear an unresolved failure."
        default: return ""
        }
    }
    private func binding(_ path: WritableKeyPath<OutcomeDraft, String>) -> Binding<String> {
        Binding(get: { draft[keyPath: path] }, set: { value in
            if path == \.save { model.setOutcomeSave(map, value) }
            else { model.updateOutcome(map) { $0[keyPath: path] = value } }
        })
    }
    private func chooseSave() {
        guard !model.fixture else { model.status = "Synthetic fixture: real save access disabled."; return }
        let choice = model.beginOutcomeSaveChoice(map)
        let panel = NSOpenPanel(); panel.title = "Choose the exact save (read only)"
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.request("history.save_digest", ["path": url.path]) { value in
                model.acceptOutcomeSaveChoice(map, choice, value.text("save_id"))
            } }
        }
    }
    private func unique(_ rows: [[String: Any]], _ key: String) -> [[String: Any]] {
        var seen = Set<String>()
        return rows.reversed().filter { !$0.text(key).isEmpty && seen.insert($0.text(key)).inserted }.reversed()
    }
}
private extension Dictionary where Key == String, Value == Any {
    var sessionID: String { text("attempt_id") }
    var incidentID: String { text("incident_id") }
}
