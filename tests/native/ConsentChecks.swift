import AppKit
import Foundation

@main struct ConsentChecks {
    @MainActor static func main() async throws {
        let backend = Backend()
        try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2]])
        let model = LauncherModel(fixture: false, backend: backend)
        model.connected = true
        let map = try MapItem(["id": "synthetic-map", "name": "Synthetic Consent Map", "archive_sha256": String(repeating: "a", count: 64), "variant_id": String(repeating: "b", count: 64)])
        let build = String(repeating: "c", count: 64)
        let tools = NativeMapToolsState()
        let target = NativeMapTarget(map, build)
        func require(_ value: Bool, _ message: String = "Native consent assertion failed", line: UInt = #line) { precondition(value, message + " at line \(line)") }
        let key = target.key
        model.maps = [map]; model.selection = map.id
        model.details[map.id] = ["context": ["game_executable_sha256": build]]
        var draft = MapToolDraft(); draft.recipe = "recipe-A"; draft.difficulty = true; draft.legacyScope = "Keep this unsaved observation"
        tools.drafts[key] = draft
        func packet(_ action: String = "create_recipe") -> NativeMapConsent { NativeMapConsent(target, action, draft) }
        for action in ["remove", "create_recipe", "create_experimental"] {
            let consent = packet(action)
            require(consent.message.hasPrefix(map.name + "\n" + target.identity + "\n\n"), "Every confirmation names its immutable map/edition/build")
            require(consent.message.contains("Recipe: recipe-A") == (action == "create_recipe"), "Only recipe consent names the frozen recipe")
            require(consent.message.contains(action == "remove" ? "Saves stay available for exact-version re-import." : "Creation and static checks do not prove gameplay."), "Confirmation preserves its action-specific limits")
        }
        func count(_ action: String) async throws -> Int {
            let rows = try await backend.call("fixture.counts")
            return rows["maptools." + action] as? Int ?? 0
        }
        await tools.confirmOperation(model, nil)
        require(try await count("create_recipe") == 0, "Cancelled consent cannot mutate")
        let selectedConsent = packet()
        model.selection = "different-map"
        await tools.confirmOperation(model, selectedConsent)
        model.selection = map.id
        await tools.confirmOperation(model, selectedConsent)
        require(try await count("create_recipe") == 0, "Selection changes consume/refuse consent; switching back cannot reuse it")
        let changed = try MapItem(["id": map.id, "name": map.name, "archive_sha256": map.archive, "variant_id": String(repeating: "e", count: 64)])
        let editionConsent = packet()
        model.maps = [changed]
        await tools.confirmOperation(model, editionConsent)
        model.maps = [map]
        let buildConsent = packet()
        model.details[map.id] = ["context": ["game_executable_sha256": String(repeating: "d", count: 64)]]
        await tools.confirmOperation(model, buildConsent)
        model.details[map.id] = ["context": ["game_executable_sha256": build]]
        let recipeConsent = packet()
        tools.drafts[key]?.recipe = "recipe-B"
        await tools.confirmOperation(model, recipeConsent)
        tools.drafts[key] = draft
        let experimentalConsent = packet("create_experimental")
        tools.drafts[key]?.difficulty = false
        await tools.confirmOperation(model, experimentalConsent)
        tools.drafts[key] = draft
        require(try await count("create_recipe") == 0)
        require(try await count("create_experimental") == 0, "Edition/build/recipe/experimental changes cannot mutate")
        let waitingConsent = packet()
        model.busy = true
        let waiting = Task { await tools.confirmOperation(model, waitingConsent) }
        try await Task.sleep(nanoseconds: 30_000_000)
        tools.drafts[key]?.recipe = "recipe-changed-while-queued"
        model.busy = false
        await waiting.value
        require(try await count("create_recipe") == 0, "Validate consent after waiting for the operation gate")
        tools.drafts[key] = draft
        let good = packet()
        require(good.target.name == map.name && good.target.variant == map.variant && good.recipe == "recipe-A")
        await tools.confirmOperation(model, good)
        await tools.confirmOperation(model, good)
        require(try await count("create_recipe") == 1, "One native consent permits one request")
        model.selection = "different-map"
        for _ in 0..<65 { await tools.confirmOperation(model, packet()) }
        model.selection = map.id
        await tools.confirmOperation(model, good)
        require(try await count("create_recipe") == 1, "Consumed consent stays unusable for the full service session")
        let last = try await backend.call("fixture.last")
        require(last.object("maptools.create_recipe").text("recipe_id") == "recipe-A")
        require(last.object("maptools.create_recipe").text("variant_id") == map.variant)
        require(tools.drafts[key]?.legacyScope == draft.legacyScope, "Unrelated drafts survive consent")
        tools.originalBuild = build
        let original = NativeMapConsent(NativeMapTarget(original: build), "activate_original")
        tools.originalBuild = String(repeating: "d", count: 64)
        await tools.confirmOperation(model, original)
        require(try await count("activate_original") == 0, "Original activation consent binds the captured build")
        tools.originalBuild = build
        let activation = NativeMapConsent(NativeMapTarget(original: build), "activate_original")
        await tools.confirmOperation(model, activation)
        await tools.confirmOperation(model, activation)
        require(try await count("activate_original") == 1)

        // A late removal reply cannot clear or update a newly selected edition.
        _ = try await backend.call("fixture.configure", ["delay": true])
        var otherDraft = MapToolDraft(); otherDraft.legacyScope = "New edition draft"
        let otherKey = NativeMapTarget(changed, build).key
        tools.drafts[otherKey] = otherDraft
        model.status = "Current edition status"
        let removing = Task { await tools.perform(model, map, build, "remove") }
        try await Task.sleep(nanoseconds: 30_000_000)
        model.maps = [changed]; model.details[map.id] = ["sentinel": "new-edition"]
        await removing.value
        require(model.maps.first?.variant == changed.variant && model.selection == map.id)
        require(model.details[map.id]?.text("sentinel") == "new-edition" && model.status == "Current edition status")
        require(tools.drafts[otherKey]?.legacyScope == "New edition draft", "Stale response cannot clear another edition draft")
        model.maps = [map]; model.details[map.id] = ["context": ["game_executable_sha256": build]]
        tools.drafts[key] = draft

        _ = try await backend.call("fixture.configure", ["warning": "Known synthetic issue"])
        await tools.beginPlay(model, map)
        let cancelled = tools.playReview
        require(cancelled?.warning == "Known synthetic issue")
        tools.cancelPlay()
        await tools.confirmPlay(model, cancelled)
        require(try await count("play_commit") == 0, "Cancelled Play review cannot launch")
        await tools.beginPlay(model, map)
        let wrongSelection = tools.playReview
        model.selection = "different-map"
        await tools.confirmPlay(model, wrongSelection)
        model.selection = map.id
        await tools.beginPlay(model, map)
        let wrongEdition = tools.playReview
        model.maps = [changed]
        await tools.confirmPlay(model, wrongEdition)
        model.maps = [map]
        await tools.beginPlay(model, map)
        let wrongBuild = tools.playReview
        model.details[map.id] = ["context": ["game_executable_sha256": String(repeating: "d", count: 64)]]
        await tools.confirmPlay(model, wrongBuild)
        model.details[map.id] = ["context": ["game_executable_sha256": build]]
        require(try await count("play_commit") == 0, "Changed Play target must be reviewed again")

        _ = try await backend.call("fixture.configure", ["warning": "Known synthetic issue", "delay": true])
        let reading = Task { await tools.beginPlay(model, map) }
        try await Task.sleep(nanoseconds: 30_000_000)
        model.selection = "different-map"
        await reading.value
        require(tools.playReview == nil, "Stale preflight cannot present another target's warning")
        model.selection = map.id
        _ = try await backend.call("fixture.configure", ["warning": "Known synthetic issue"])
        await tools.beginPlay(model, map)
        let validPlay = tools.playReview
        await tools.confirmPlay(model, validPlay)
        await tools.confirmPlay(model, validPlay)
        require(try await count("play_commit") == 1, "One review cannot launch twice")
        _ = try await backend.call("fixture.configure")
        await tools.beginPlay(model, map)
        require(try await count("play_commit") == 2 && tools.playReview == nil, "Normal no-warning Play does not need a warning dialog")
        _ = try await backend.call("fixture.configure", ["preparation": true])
        await tools.beginPlay(model, map)
        let preparedPlay = tools.playReview
        require(preparedPlay?.preparationTitle == "Synthetic compatibility edition")
        require(try await count("play_commit") == 2, "Compatibility preparation needs explicit review")
        await tools.confirmPlay(model, preparedPlay)
        require((try await backend.call("fixture.last")).object("maptools.play_commit")["prepare_compatibility"] as? Bool == true)
        await tools.beginPlay(model, map)
        let originalChoice = tools.playReview!
        await tools.beginOriginalPlay(model, originalChoice)
        require((try await backend.call("fixture.last")).object("maptools.play_commit")["prepare_compatibility"] as? Bool == false, "Play Original gets a fresh review without preparing")
        await tools.beginOriginalPlay(model, originalChoice)
        require(try await count("play_commit") == 4, "The consumed compatibility review cannot replay original Play")
        _ = try await backend.call("fixture.configure", ["warning": "Known issue", "reject_commit": true])
        await tools.beginPlay(model, map)
        await tools.confirmPlay(model, tools.playReview)
        require(model.error.hasPrefix("Native launch outcome unknown") && tools.unknown.contains("play:" + target.key))
        await tools.beginPlay(model, map)
        require(try await count("play_commit") == 5, "Unknown launch cannot retry even with live transport")
        require(tools.drafts[key]?.legacyScope == draft.legacyScope)
        _ = try await backend.call("fixture.configure")
        model.destination = "Original Game"
        await tools.beginPlay(model, nil)
        require(try await count("play_commit") == 6, "Original Game uses the same native launch boundary")
        model.destination = "Map Library"
        require((try await backend.call("fixture.last")).object("maptools.play_commit")["launch_deadline"] == nil, "Ordinary UI Play stays nonfinite")

        // Exercise the real Swift model/consent/stdio composition. The Python
        // service owns deadline validation; the UI forwards one fixed value.
        let fixedDeadline = 123456.125
        let finite = LauncherModel(fixture: false, backend: backend, arguments: ["launcher", "--play-deadline", String(fixedDeadline)])
        finite.connected = true; finite.maps = [map]; finite.selection = map.id
        finite.details[map.id] = model.details[map.id]
        let finiteTools = NativeMapToolsState()
        _ = try await backend.call("fixture.configure", ["warning": "Known synthetic issue"])
        await finiteTools.beginPlay(finite, map)
        var finiteLast = try await backend.call("fixture.last")
        require(finiteLast.object("maptools.play_preflight")["launch_deadline"] as? Double == fixedDeadline)
        let finiteReview = finiteTools.playReview
        finite.busy = true
        let queuedFinite = Task { await finiteTools.confirmPlay(finite, finiteReview) }
        try await Task.sleep(nanoseconds: 30_000_000)
        finite.busy = false
        await queuedFinite.value
        finiteLast = try await backend.call("fixture.last")
        require(finiteLast.object("maptools.play_commit")["launch_deadline"] as? Double == fixedDeadline, "Consent/waiting cannot renew the owner deadline")
        _ = try await backend.call("fixture.configure", ["preparation": true])
        await finiteTools.beginPlay(finite, map)
        await finiteTools.beginOriginalPlay(finite, finiteTools.playReview!)
        finiteLast = try await backend.call("fixture.last")
        require(finiteLast.object("maptools.play_commit")["launch_deadline"] as? Double == fixedDeadline && finiteLast.object("maptools.play_commit")["prepare_compatibility"] as? Bool == false, "Play Original retains the fixed gate")
        for invalid in [["--play-deadline"], ["--play-deadline", "nan"], ["--play-deadline", "inf"], ["--play-deadline", "0"], ["--play-deadline", "-1"], ["--play-deadline", "bad"], ["--play-deadline", "1", "--play-deadline", "2"]] {
            let refused = LauncherModel(fixture: false, arguments: ["launcher"] + invalid)
            await refused.start()
            require(!refused.connected && refused.loaded && refused.error.hasPrefix("--play-deadline requires"), "Malformed finite options cannot start an unbounded service")
        }
        _ = try await backend.call("fixture.configure", ["reject": true, "error": "Map tool outcome needs inspection; do not repeat the action: History append failed; partial write needs inspection"])
        await tools.perform(model, map, build, "link_issue")
        await tools.perform(model, map, build, "link_issue")
        require(tools.unknown.contains(key) && tools.drafts[key]?.legacyScope == draft.legacyScope)
        require(try await count("link_issue") == 1, "Partial history result holds UNKNOWN without retry")
        print("Native consent checks passed: cancellation, selection/edition/build/recipe changes, queued changes, immutable fields, single use, stale replies, warning review, no-warning Play, original build and unknown holds")
    }
}
