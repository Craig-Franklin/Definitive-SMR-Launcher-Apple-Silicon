import AppKit
import SwiftUI

private typealias ToolViewState<Value> = SwiftUI.State<Value>

struct NativeMapTarget {
    let profile: String
    let archive: String
    let variant: String
    let build: String
    let name: String
    init(_ map: MapItem, _ build: String) {
        profile = map.id; archive = map.archive; variant = map.variant; self.build = build; name = map.name
    }
    init(original build: String) {
        profile = "original-game"; archive = ""; variant = ""; self.build = build; name = "Original Game"
    }
    var key: String { [profile, archive, variant, build].joined(separator: ":") }
    var parameters: [String: Any] {
        ["profile_id": profile, "archive_sha256": archive, "variant_id": variant, "game_executable_sha256": build]
    }
    func matches(_ data: [String: Any]) -> Bool {
        data.text("profile_id") == profile && data.text("archive_sha256") == archive &&
        data.text("variant_id") == variant && data.text("game_executable_sha256") == build
    }
    var identity: String { "Edition: \(variant.isEmpty ? "Original Game" : variant)\nGame build: \(build)" }
}

struct NativeMapConsent {
    let id = UUID()
    let target: NativeMapTarget
    let action: String
    let recipe: String
    let difficulty: Bool
    init(_ target: NativeMapTarget, _ action: String, _ draft: MapToolDraft = MapToolDraft()) {
        self.target = target; self.action = action; recipe = draft.recipe; difficulty = draft.difficulty
    }
    var fields: [String: Any] {
        action == "create_recipe" ? ["recipe_id": recipe] :
        action == "create_experimental" ? ["difficulty": difficulty, "editor": false] : [:]
    }
    func matchesDraft(_ draft: MapToolDraft) -> Bool {
        (action != "create_recipe" || recipe == draft.recipe) &&
        (action != "create_experimental" || difficulty == draft.difficulty)
    }
    var message: String {
        let recipeText = action == "create_recipe" ? "Recipe: \(recipe)\n\n" : ""
        let explanation: String
        if action == "remove" {
            explanation = "Removes this edition and unshared launcher-owned archives. Saves stay available for exact-version re-import. Source files outside the launcher stay. Quit Railroads and switch to Original Game first."
        } else {
            explanation = "Creates or reuses a separate edition with its own saves. Original map and saves stay intact. Creation and static checks do not prove gameplay. Unsupported inputs will be refused."
        }
        return "\(target.name)\n\(target.identity)\n\n\(recipeText)\(explanation)"
    }
}

struct NativePlayConsent {
    let intent: UUID
    let target: NativeMapTarget
    let ticket: String
    let fingerprint: String
    let warning: String
    let prepareCompatibility: Bool
    let preparationTitle: String
}

struct MapToolDraft {
    var operation = UUID().uuidString
    var scenario = ""
    var issueURL = ""
    var linkURL = ""
    var scope = "exact"
    var kind = "fix_proposed"
    var fix = ""
    var failure = ""
    var attempt = ""
    var legacyScope = ""
    var legacyIssue = ""
    var checks: Set<String> = []
    var recipe = ""
    var difficulty = false
    var dirty: Bool {
        !linkURL.isEmpty || !issueURL.isEmpty || !fix.isEmpty || !failure.isEmpty || !attempt.isEmpty ||
        !legacyScope.isEmpty || !legacyIssue.isEmpty || !checks.isEmpty || kind != "fix_proposed"
    }
    mutating func completed(_ action: String) {
        if action == "legacy_record" { legacyScope = ""; legacyIssue = ""; checks = [] }
        if action == "link_issue" { linkURL = "" }
        if action == "record_fix" {
            operation = UUID().uuidString; kind = "fix_proposed"; fix = ""; failure = ""; attempt = ""; issueURL = ""
        }
    }
}

@MainActor final class NativeMapToolsState: ObservableObject {
    @Published var drafts: [String: MapToolDraft] = [:]
    @Published var presentations: [String: [String: Any]] = [:]
    @Published var messages: [String: String] = [:]
    @Published var errors: [String: String] = [:]
    @Published var pending: Set<String> = []
    @Published var unknown: Set<String> = []
    @Published var originalBuild = ""
    @Published var playReview: NativePlayConsent?
    @Published var playPending = false
    private var playIntent = UUID()
    private var usedConsents: Set<UUID> = []
    var dirty: Bool { drafts.values.contains { $0.dirty } }

    static func key(_ map: MapItem, _ build: String) -> String {
        [map.id, map.archive, map.variant, build].joined(separator: ":")
    }
    func current(_ model: LauncherModel, _ target: NativeMapTarget) -> Bool {
        if target.profile == "original-game" { return !target.build.isEmpty && originalBuild == target.build }
        guard model.selection == target.profile, let selected = model.selected else { return false }
        let build = model.details[selected.id]?.object("context").text("game_executable_sha256") ?? ""
        return !build.isEmpty && NativeMapTarget(selected, build).key == target.key
    }
    private func currentPlay(_ model: LauncherModel, _ target: NativeMapTarget) -> Bool {
        current(model, target) && model.destination == (target.profile == "original-game" ? "Original Game" : "Map Library")
    }
    private func waitForOperation(_ model: LauncherModel) async -> Bool {
        while model.working {
            do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return false }
        }
        return !Task.isCancelled
    }
    func confirmOperation(_ model: LauncherModel, _ consent: NativeMapConsent?) async {
        guard let consent, await waitForOperation(model) else { return }
        guard usedConsents.insert(consent.id).inserted else { return }
        let target = consent.target
        guard current(model, target), consent.matchesDraft(drafts[target.key] ?? MapToolDraft()) else {
            errors[target.key] = "The selected map, edition, build or recipe changed. Review the action again."
            return
        }
        if consent.action == "activate_original" {
            let key = "original:" + target.build
            guard !pending.contains(key), !unknown.contains(key) else { return }
            pending.insert(key)
            var received = false
            await model.request("maptools.activate_original", target.parameters.merging(["confirmed": true]) { _, new in new }) { result in
                received = true
                if self.current(model, target) { model.status = result.text("message") }
            }
            if !received && (!model.connected || model.error.hasPrefix("Map tool outcome needs inspection")) { unknown.insert(key) }
            pending.remove(key)
        } else if let map = model.selected {
            await perform(model, map, target.build, consent.action, consent.fields)
        }
    }
    func cancelPlay() { playIntent = UUID(); playReview = nil }
    func beginPlay(_ model: LauncherModel, _ map: MapItem?, prepareCompatibility: Bool = true) async {
        guard !playPending else { return }
        playPending = true; cancelPlay()
        let intent = playIntent
        defer { playPending = false }
        let target: NativeMapTarget
        if let map {
            let build = model.details[map.id]?.object("context").text("game_executable_sha256") ?? ""
            target = NativeMapTarget(map, build)
        } else {
            var received = false
            await model.request("maptools.original_context") { result in
                received = true; self.originalBuild = result.text("game_executable_sha256")
            }
            guard received, intent == playIntent, model.destination == "Original Game" else { return }
            target = NativeMapTarget(original: originalBuild)
        }
        guard currentPlay(model, target) else {
            if target.build.isEmpty, model.selection == target.profile {
                model.error = "The current map/build identity is unavailable. Read map details before Play."
            }
            return
        }
        guard !unknown.contains("play:" + target.key) else {
            model.error = "The previous native launch outcome is unknown. Inspect the active profile and game before a new launch."
            return
        }
        var params = target.parameters
        params["prepare_compatibility"] = prepareCompatibility
        if let deadline = model.playDeadline { params["launch_deadline"] = deadline }
        await model.request("maptools.play_preflight", params) { result in
            guard self.playIntent == intent, self.currentPlay(model, target),
                  target.matches(result.object("target")), !result.text("ticket").isEmpty,
                  !result.text("fingerprint").isEmpty else { return }
            self.playReview = NativePlayConsent(intent: intent, target: target, ticket: result.text("ticket"),
                fingerprint: result.text("fingerprint"), warning: result.text("warning"),
                prepareCompatibility: prepareCompatibility, preparationTitle: result.object("preparation").text("title"))
        }
        if let review = playReview, review.intent == intent, review.warning.isEmpty {
            await confirmPlay(model, review)
        }
    }
    func beginOriginalPlay(_ model: LauncherModel, _ review: NativePlayConsent) async {
        guard playIntent == review.intent, currentPlay(model, review.target), !review.preparationTitle.isEmpty else { return }
        await beginPlay(model, model.selected, prepareCompatibility: false)
    }
    func confirmPlay(_ model: LauncherModel, _ review: NativePlayConsent?) async {
        guard let review, await waitForOperation(model) else { return }
        let target = review.target
        guard playIntent == review.intent else { return }
        guard currentPlay(model, target),
              (target.profile != "original-game" || model.destination == "Original Game"),
              !unknown.contains("play:" + target.key) else { cancelPlay(); return }
        // Consume native consent before the request. Backend tickets are also one-use.
        cancelPlay()
        var params = target.parameters
        params["ticket"] = review.ticket; params["fingerprint"] = review.fingerprint; params["confirmed"] = true
        params["prepare_compatibility"] = review.prepareCompatibility
        if let deadline = model.playDeadline { params["launch_deadline"] = deadline }
        var received = false
        await model.request("maptools.play_commit", params) { result in
            received = true
            if self.currentPlay(model, target), target.matches(result.object("target")) { model.status = result.text("message") }
        }
        if !received && (!model.connected || model.error.hasPrefix("Native launch outcome unknown") || model.error.hasPrefix("Map tool outcome needs inspection")) {
            unknown.insert("play:" + target.key)
        }
    }
    static func scope(_ map: MapItem, _ build: String) -> [String: Any] {
        ["profile_id": map.id, "archive_sha256": map.archive, "variant_id": map.variant,
         "game_executable_sha256": build]
    }
    func load(_ model: LauncherModel, _ map: MapItem, _ build: String, force: Bool = false) async {
        let key = Self.key(map, build)
        guard !build.isEmpty, !pending.contains(key), force || presentations[key] == nil else { return }
        pending.insert(key)
        if model.fixture {
            let detail = model.details[map.id] ?? [:]
            presentations[key] = ["history": detail.rows("history"), "scenarios": detail["scenarios"] ?? [],
                "issues": [], "recipes": [], "legacy_checks": Self.fixtureChecks,
                "legacy_text": "Synthetic fixture. No legacy result recorded.",
                "checks_text": "Synthetic fixture. No import scan executed.",
                "scope_text": "Synthetic identities only. Exact scenario evidence and legacy reports remain separate."]
        } else {
            var received = false
            await model.request("maptools.inspect", Self.scope(map, build)) { result in
                received = true; self.presentations[key] = result; self.errors[key] = nil
            }
            if !received { errors[key] = model.error }
        }
        pending.remove(key)
    }
    func perform(_ model: LauncherModel, _ map: MapItem, _ build: String, _ action: String,
                 _ fields: [String: Any] = [:]) async {
        let key = Self.key(map, build)
        guard !pending.contains(key), !unknown.contains(key), !build.isEmpty else { return }
        pending.insert(key); errors[key] = nil; messages[key] = nil
        var params = Self.scope(map, build)
        params.merge(fields) { _, new in new }; params["confirmed"] = true
        var received = false
        await model.request("maptools." + action, params) { result in
            received = true
            self.messages[key] = result.text("message", "Done")
            if let url = URL(string: result.text("url")), action == "issue_url" {
                if !NSWorkspace.shared.open(url) { self.errors[key] = "The browser could not open this linked issue." }
            }
            if ["legacy_record", "link_issue", "record_fix"].contains(action) {
                self.drafts[key]?.completed(action)
                model.mapToolDraftsPresent = self.dirty
            }
            if action == "refresh_checks" { self.presentations[key]?["checks_text"] = result.text("checks_text"); self.presentations[key]?["checks_error"] = nil }
            if action == "remove" {
                let matching = model.maps.contains { $0.id == map.id && $0.archive == map.archive && $0.variant == map.variant }
                model.maps.removeAll { $0.id == map.id && $0.archive == map.archive && $0.variant == map.variant }
                if matching { model.details[map.id] = nil }
                if matching && model.selection == map.id { model.selection = nil; model.narrowDetail = false }
                self.drafts[key] = nil; model.mapToolDraftsPresent = self.dirty
                if matching { model.status = result.text("message") }
            }
        }
        if !received && !model.fixture {
            errors[key] = model.error
            // A lost transport never makes another mutation safe. Keep the draft
            // for inspection and hold resubmission for the rest of this session.
            if !model.connected || model.error.hasPrefix("Map tool outcome needs inspection") { unknown.insert(key) }
        }
        pending.remove(key)
        if received && ["legacy_record", "link_issue", "record_fix", "refresh_issues"].contains(action) {
            await load(model, map, build, force: true)
            await model.loadDetails(map.id, force: true)
        }
    }
    static let fixtureChecks: [[String: Any]] = [
        ["id": "scenario_loaded", "label": "Fresh scenario loaded successfully"],
        ["id": "gameplay", "label": "Track, stations and trains worked during gameplay"],
        ["id": "autosave_written", "label": "An autosave was written"],
        ["id": "manual_save_written", "label": "A manual save was written"],
        ["id": "manual_save_reloaded", "label": "Quit, reopened the game and loaded the manual save"],
        ["id": "continued_after_reload", "label": "Continued playing successfully after reloading"]
    ]
}

struct NativeMapToolsView: View {
    @ObservedObject var model: LauncherModel
    @ObservedObject var tools: NativeMapToolsState
    let map: MapItem
    let section: String
    @ToolViewState private var confirmation: NativeMapConsent?
    var build: String { model.details[map.id]?.object("context").text("game_executable_sha256") ?? "" }
    var key: String { NativeMapToolsState.key(map, build) }
    var data: [String: Any] { tools.presentations[key] ?? [:] }
    var draft: MapToolDraft { tools.drafts[key] ?? MapToolDraft() }
    var events: [[String: Any]] { data.rows("history") }
    var scenarios: [String] { data["scenarios"] as? [String] ?? [] }
    var held: Bool { model.working || tools.pending.contains(key) || tools.unknown.contains(key) || build.isEmpty || !model.connected }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if tools.pending.contains(key) { ProgressView("Reading current map tools…").controlSize(.small) }
            if let error = tools.errors[key], !error.isEmpty { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            if tools.unknown.contains(key) { Text("The unfinished result is unknown. This draft is retained; inspect stored results before any new confirmation.").foregroundStyle(.orange) }
            if let message = tools.messages[key] { Text(message).textSelection(.enabled) }
            if tools.presentations[key] == nil {
                Text("Map tools need current edition and build identities. Existing drafts stay available.").foregroundStyle(.secondary)
                Button("Read Map Tools") { Task { await tools.load(model, map, build, force: true) } }.disabled(held)
            } else {
                switch section {
                case "Legacy Mac Test": legacy
                case "Compatibility & Editions": compatibility
                case "History Scope": Text(data.text("scope_text")).font(.caption).textSelection(.enabled)
                default: issues
                }
                Button("Refresh Map Tools") { Task { await tools.load(model, map, build, force: true) } }.disabled(model.working || tools.pending.contains(key))
            }
        }
        .task(id: key) { await tools.load(model, map, build) }
        .onChange(of: key) { _ in confirmation = nil }
        .onChange(of: model.selection) { _ in confirmation = nil }
        .onChange(of: draft.recipe) { _ in if confirmation?.action == "create_recipe" { confirmation = nil } }
        .onChange(of: draft.difficulty) { _ in if confirmation?.action == "create_experimental" { confirmation = nil } }
        .confirmationDialog(confirmation?.action == "remove" ? "Remove this map edition?" : "Create a separate edition?",
                            isPresented: Binding(get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }), titleVisibility: .visible) {
            if let consent = confirmation {
                Button(consent.action == "remove" ? "Remove Map & Keep Saves" : "Create Separate Edition", role: consent.action == "remove" ? .destructive : nil) {
                    Task { await tools.confirmOperation(model, consent) }
                }
            }
            Button("Cancel", role: .cancel) { confirmation = nil }
        } message: {
            if let consent = confirmation {
                Text(consent.message)
            }
        }
    }
    private var legacy: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Legacy Mac Test").font(.headline)
            Text("After playing, quit Railroads before recording. Check only what you personally tested on the active edition.")
            Text("This limited report cannot earn Verified or record exact scenario checkpoints. Known issues remain historical warnings.").font(.caption).foregroundStyle(.secondary)
            Text(data.text("legacy_error", data.text("legacy_text"))).textSelection(.enabled)
            ForEach(data.rows("legacy_checks"), id: \.selfID) { check in
                Toggle(check.text("label"), isOn: Binding(get: { draft.checks.contains(check.text("id")) }, set: { value in
                    update { if value { $0.checks.insert(check.text("id")) } else { $0.checks.remove(check.text("id")) } }
                }))
            }
            TextField("Test scope / duration (required, 500 characters maximum)", text: binding(\.legacyScope), axis: .vertical)
            TextField("Crash or other issue (optional, 500 characters maximum)", text: binding(\.legacyIssue), axis: .vertical)
            Button("Save My Legacy Test Result") {
                let checks = Dictionary(uniqueKeysWithValues: data.rows("legacy_checks").map { ($0.text("id"), draft.checks.contains($0.text("id"))) })
                run("legacy_record", ["scope": draft.legacyScope, "issue": draft.legacyIssue, "checks": checks])
            }.buttonStyle(.borderedProminent).disabled(draft.legacyScope.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            Button("Exact Scenario Outcome…") { model.record(map) }
            discard
        }.disabled(held)
    }
    private var issues: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Issues & Fixes").font(.headline)
            Text("Implementation, fix testing, issue state and game completion are independent. Known failures stay visible.")
            if data.rows("issues").isEmpty { Text("No linked issue. Unchecked issue state remains unknown.").foregroundStyle(.secondary) }
            ForEach(Array(data.rows("issues").enumerated()), id: \.offset) { _, issue in
                VStack(alignment: .leading, spacing: 6) {
                    Text(issue.text("label") + " · " + issue.text("scope") + " scope").fontWeight(.medium)
                    Text(issue.text("scenario", "Scenario unknown")).font(.caption)
                    Text("Last successful check: " + issue.text("checked_at", "None")).font(.caption)
                    Button(issue.text("url")) { run("issue_url", ["issue_url": issue.text("url")]) }.lineLimit(2)
                }
            }
            Button("Refresh Issue States") { run("refresh_issues") }.disabled(held)
            Text("Refresh sends only linked personal-repository issue numbers. It does not change GitHub issues.").font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Link an Issue") {
                VStack(alignment: .leading, spacing: 12) {
                    scenarioPicker
                    Picker("Applicability", selection: binding(\.scope)) {
                        Text("Exact scenario / edition").tag("exact")
                        Text("Related original-map context").tag("related")
                    }
                    TextField("Personal-repository issue URL", text: binding(\.linkURL))
                    Button("Link Issue Locally") { run("link_issue", ["scenario": draft.scenario, "scope": draft.scope, "issue_url": draft.linkURL]) }
                    discard
                }.padding(.top, 8).disabled(held)
            }
            DisclosureGroup("Record Fix Implementation or Test") {
                VStack(alignment: .leading, spacing: 12) {
                    scenarioPicker
                    Picker("Fact I confirm", selection: binding(\.kind)) {
                        Text("Fix proposed").tag("fix_proposed"); Text("Fix implemented").tag("fix_implemented")
                        Text("Fix test passed").tag("fix_test_passed"); Text("Fix test failed").tag("fix_test_failed")
                    }
                    Picker("Fix identity", selection: binding(\.fix)) {
                        Text(draft.kind.hasPrefix("fix_test") ? "Choose implemented fix" : "New fix").tag("")
                        ForEach(unique(events.filter { ["fix_proposed", "fix_implemented"].contains($0.text("kind")) && $0.text("scenario") == draft.scenario }, "fix_id"), id: \.selfID) { row in
                            Text(row.text("label") + " · " + row.text("fix_id").prefix(12)).tag(row.text("fix_id"))
                        }
                    }
                    if draft.kind.hasPrefix("fix_test") {
                        Picker("Attributed failure being tested", selection: binding(\.failure)) {
                            Text("Choose a failure with known checkpoint").tag("")
                            ForEach(events.filter { ["crash_load", "crash_play", "crash_unknown", "resource_stop"].contains($0.text("kind")) && !$0.text("checkpoint").isEmpty && $0.text("scenario") == draft.scenario }, id: \.selfID) { row in
                                Text(row.text("label") + " · " + row.text("checkpoint") + " · " + row.text("event_id").prefix(12)).tag(row.text("event_id"))
                            }
                        }
                        Picker("Confirmed test open", selection: binding(\.attempt)) {
                            Text("Choose the exact test open").tag("")
                            ForEach(unique(events.filter { $0.text("kind") == "process_confirmed" }, "attempt_id"), id: \.selfID) { row in
                                Text(row.text("at") + " · " + row.text("attempt_id").prefix(12)).tag(row.text("attempt_id"))
                            }
                        }
                        Text("A pass requires an implemented fix, a new confirmed open after implementation, and recorded success at the failed checkpoint in this exact scope. Record that checkpoint in Exact Scenario Outcome first.").font(.caption)
                        Button("Exact Scenario Outcome…") { model.record(map) }
                    }
                    Picker("Applicable linked issue", selection: binding(\.issueURL)) {
                        Text("No linked issue").tag("")
                        ForEach(data.rows("issues").filter { $0.text("scope") == "exact" && $0.text("scenario") == draft.scenario }, id: \.selfID) { row in Text(row.text("url")).tag(row.text("url")) }
                    }
                    Button("Record My Confirmed Fact") {
                        run("record_fix", ["operation_id": draft.operation, "scenario": draft.scenario, "kind": draft.kind,
                            "fix_id": draft.fix, "issue_url": draft.issueURL, "target_id": draft.failure, "attempt_id": draft.attempt])
                    }.buttonStyle(.borderedProminent).disabled(draft.scenario.isEmpty)
                    discard
                }.padding(.top, 8).disabled(held)
            }
            Text(data.text("fixes_text", "No fix facts recorded.")).textSelection(.enabled)
            Button("Compatibility & Editions…") { model.section = "Compatibility & Editions" }
            Button("History Scope…") { model.section = "History Scope" }
        }
    }
    private var compatibility: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Compatibility & Editions").font(.headline)
            Text("Separate editions retain separate saves. Successful creation and static checks do not prove gameplay compatibility.")
            Text(data.text("checks_error", data.text("checks_text"))).textSelection(.enabled)
            Button("Run Import Checks") { run("refresh_checks") }.disabled(held)
            if let error = data["recipes_error"] as? String { Text(error).foregroundStyle(.orange) }
            else if data.rows("recipes").isEmpty { Text("No reviewed repair recipe is available for this edition. This does not establish incompatibility.") }
            else {
                Picker("Compatibility recipe", selection: binding(\.recipe)) {
                    Text("Choose a reviewed recipe").tag("")
                    ForEach(data.rows("recipes"), id: \.selfID) { row in Text(row.text("title")).tag(row.text("id")) }
                }
                if let recipe = data.rows("recipes").first(where: { $0.text("id") == draft.recipe }) { Text(recipe.text("description")).textSelection(.enabled) }
                Button("Create Compatibility Edition…") { openConsent("create_recipe") }.disabled(held || draft.recipe.isEmpty)
            }
            DisclosureGroup("Experimental Editions") {
                VStack(alignment: .leading, spacing: 12) {
                    Toggle("Use Windows launcher custom difficulty levels", isOn: flag(\.difficulty))
                    Text("Custom difficulty is experimental on Mac and refused when the map supplies its own definitions. New editions start with empty saves. Terrain editor remains unavailable until exported maps can be captured safely.").font(.caption)
                    Button("Create Experimental Edition…") { openConsent("create_experimental") }.disabled(held || !draft.difficulty)
                    discard
                }.padding(.top, 8)
            }
            Text("After creating an edition, use Refresh Library to browse it. Your search, filter and drafts stay in place.").font(.caption).foregroundStyle(.secondary)
            Divider()
            OriginalActivationControls(model: model, tools: tools)
            Button("Remove Map…", role: .destructive) { openConsent("remove") }.disabled(held)
        }
    }
    private var scenarioPicker: some View {
        Picker("Exact scenario", selection: binding(\.scenario)) {
            Text("Choose scenario / unknown").tag("")
            ForEach(scenarios, id: \.self) { Text($0).tag($0) }
        }
    }
    private var discard: some View {
        Button("Discard This Tool Draft", role: .destructive) {
            tools.drafts[key] = nil; model.mapToolDraftsPresent = tools.dirty
        }.disabled(!draft.dirty || tools.unknown.contains(key))
    }
    private func update(_ change: (inout MapToolDraft) -> Void) {
        var value = draft; change(&value)
        tools.drafts[key] = value; tools.messages[key] = nil; model.mapToolDraftsPresent = tools.dirty
    }
    private func binding(_ path: WritableKeyPath<MapToolDraft, String>) -> Binding<String> {
        Binding(get: { draft[keyPath: path] }, set: { value in update { $0[keyPath: path] = value } })
    }
    private func flag(_ path: WritableKeyPath<MapToolDraft, Bool>) -> Binding<Bool> {
        Binding(get: { draft[keyPath: path] }, set: { value in update { $0[keyPath: path] = value } })
    }
    private func openConsent(_ action: String) {
        confirmation = NativeMapConsent(NativeMapTarget(map, build), action, draft)
    }
    private func run(_ action: String, _ fields: [String: Any] = [:]) {
        let submittedBuild = build
        Task { await tools.perform(model, map, submittedBuild, action, fields) }
    }
    private func unique(_ rows: [[String: Any]], _ field: String) -> [[String: Any]] {
        var seen: Set<String> = []
        return rows.reversed().filter { seen.insert($0.text(field)).inserted }
    }
}

// Stable presentation identifiers only; mutation inputs retain complete digests.
private extension Dictionary where Key == String, Value == Any {
    var selfID: String { [text("event_id"), text("fix_id"), text("id"), text("url"), text("scenario"), text("scope")].joined(separator: ":") }
}

struct OriginalActivationControls: View {
    @ObservedObject var model: LauncherModel
    @ObservedObject var tools: NativeMapToolsState
    @ToolViewState private var confirmation: NativeMapConsent?
    var build: String { tools.originalBuild }
    var actionKey: String { "original:" + build }
    var pending: Bool { tools.pending.contains(actionKey) }
    var heldUnknown: Bool { tools.unknown.contains(actionKey) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button("Activate Original Game Without Launch…") { confirmation = NativeMapConsent(NativeMapTarget(original: build), "activate_original") }
                .disabled(model.working || pending || heldUnknown || build.isEmpty || !model.enrolled || !model.connected)
            Text("Quit Railroads first. Existing preservation and stopped-game guards apply.").font(.caption).foregroundStyle(.secondary)
            if heldUnknown { Text("Activation result is unknown. Inspect the active profile before trying again.").foregroundStyle(.orange) }
            if let error = tools.errors[NativeMapTarget(original: build).key] { Text(error).foregroundStyle(.red) }
            if build.isEmpty {
                Button("Read Current Activation Identity") { Task { await readIdentity() } }.disabled(model.working || !model.connected)
            }
        }
        .task { await readIdentity() }
        .onChange(of: build) { _ in confirmation = nil }
        .confirmationDialog("Activate Original Game without launching?", isPresented: Binding(get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }), titleVisibility: .visible) {
            if let consent = confirmation {
                Button("Activate Original Game") { Task { await tools.confirmOperation(model, consent) } }
            }
            Button("Cancel", role: .cancel) { confirmation = nil }
        } message: {
            if let consent = confirmation {
                Text(consent.target.name + "\n" + consent.target.identity + "\n\nSwitches the active profile to Original Game and preserves each profile's saves. No game will launch. A running game or pending recovery will refuse this action.")
            }
        }
    }
    private func readIdentity() async {
        if model.fixture { tools.originalBuild = String(repeating: "f", count: 64) }
        else { await model.request("maptools.original_context") { tools.originalBuild = $0.text("game_executable_sha256") } }
    }
}

struct NativePlayReviewModifier: ViewModifier {
    @ObservedObject var model: LauncherModel
    @ObservedObject var tools: NativeMapToolsState
    private var selectedIdentity: String {
        guard let map = model.selected else { return "" }
        return NativeMapTarget(map, model.details[map.id]?.object("context").text("game_executable_sha256") ?? "").key
    }
    func body(content: Content) -> some View {
        content
            .onChange(of: model.selection) { _ in tools.cancelPlay() }
            .onChange(of: model.destination) { _ in tools.cancelPlay() }
            .onChange(of: selectedIdentity) { _ in
                if tools.playReview?.target.profile != "original-game" { tools.cancelPlay() }
            }
            .onChange(of: tools.originalBuild) { _ in
                if tools.playReview?.target.profile == "original-game" { tools.cancelPlay() }
            }
            .confirmationDialog(tools.playReview?.preparationTitle.isEmpty == false ? "Compatibility edition available" : "Known map issue", isPresented: Binding(get: { tools.playReview != nil && tools.playReview?.warning.isEmpty == false }, set: { if !$0 { tools.playReview = nil } }), titleVisibility: .visible) {
                if let review = tools.playReview {
                    Button(review.preparationTitle.isEmpty ? "Launch This Edition Anyway" : "Prepare & Play Compatibility Edition") { Task { await tools.confirmPlay(model, review) } }
                    if !review.preparationTitle.isEmpty {
                        Button("Play Original Edition") { Task { await tools.beginOriginalPlay(model, review) } }
                    }
                }
                Button("Cancel", role: .cancel) { tools.cancelPlay() }
            } message: {
                if let review = tools.playReview { Text(review.target.name + "\n" + review.target.identity + "\n\n" + review.warning) }
            }
    }
}
