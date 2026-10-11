import AppKit
import Combine
import Foundation

struct MapItem: Identifiable {
    let id: String
    let packageName: String
    let archive: String
    let variant: String
    let metadata: [String: Any]
    let verification: String
    let notice: String
    let summary: [String: Any]
    let historyError: String
    let title: String
    let notes: String
    let imagePNG: String
    let packageImage: NSImage?
    let image: NSImage?
    var name: String { title.isEmpty ? packageName : title }
    var status: String {
        let history = summary.text("label", verification)
        if verification == "Known issue" && !history.hasPrefix("Known issue") { return "Known issue · " + history }
        if !historyError.isEmpty { return "History needs inspection · " + history }
        if verification == "Needs inspection" || !notice.isEmpty { return "Needs inspection · " + history }
        return history
    }
    var warning: Bool { verification == "Known issue" || ["amber", "red"].contains(summary.text("light")) || !historyError.isEmpty || !notice.isEmpty }

    init(_ data: [String: Any]) throws {
        guard !data.text("id").isEmpty, !data.text("archive_sha256").isEmpty, !data.text("variant_id").isEmpty else {
            throw NativeError.message("The map presentation has no stable identity. Refresh the library before using it.")
        }
        id = data.text("id"); packageName = data.text("name")
        archive = data.text("archive_sha256"); variant = data.text("variant_id")
        metadata = data.object("metadata"); verification = data.text("verification", "Not verified")
        notice = data.text("notice"); summary = data.object("summary"); historyError = data.text("history_error")
        let annotation = data.object("annotation")
        title = annotation.text("title"); notes = annotation.text("notes"); imagePNG = annotation.text("image_png")
        let packageArt = data.text("art")
        packageImage = packageArt.utf8.count <= 1_400_000 ? Data(base64Encoded: packageArt).flatMap(NSImage.init(data:)) : nil
        let artwork = imagePNG.isEmpty ? packageArt : imagePNG
        if artwork.utf8.count <= 1_400_000, let bytes = Data(base64Encoded: artwork) {
            image = NSImage(data: bytes)
        } else { image = nil }
    }
}

struct PersonalDraft {
    var title: String
    var notes: String
    var imagePNG: String?
    var dirty = false
}
struct OutcomeDraft {
    var outcome = ""
    var attempt = ""
    var scenario = ""
    var game = ""
    var save = ""
    var reason = ""
    var problem = "crash_unknown"
    var incident = ""
    var checkpoint = ""
    var continued = false
}
final class ScrollMemory { var point: NSPoint? }

@MainActor final class LauncherModel: ObservableObject {
    @Published var maps: [MapItem] = []
    @Published var selection: String?
    @Published var destination = "Map Library"
    @Published var section = "Overview"
    @Published var query = ""
    @Published var filter = "All maps"
    @Published var sort = "Name A–Z"
    @Published var status = "Opening your library…"
    @Published var crashStatus = ""
    @Published var error = ""
    @Published var busy = false
    @Published private(set) var serviceBusy = false
    @Published var loaded = false
    @Published var available = false
    @Published var enrolled = false
    @Published var connected = false
    @Published var details: [String: [String: Any]] = [:]
    @Published var personal: [String: PersonalDraft] = [:]
    @Published var outcomes: [String: OutcomeDraft] = [:]
    @Published var preferences: [String: Any] = [:]
    @Published var activity: [[String: Any]] = []
    @Published var collection: [[String: Any]] = []
    @Published var collectionNotice = ""
    @Published var collectionQuery = ""
    @Published var selectedRemote: String?
    @Published var narrowDetail = false
    @Published var exitPrompt: [String: Any]?
    let fixture: Bool
    let gridScroll = ScrollMemory()
    var detailScroll: [String: ScrollMemory] = [:]
    private let backend: Backend
    private var quitting = false
    private var started = false
    private var pendingRefresh = false
    var selected: MapItem? { maps.first { $0.id == selection } }
    var working: Bool { busy || serviceBusy }
    var dirty: Bool { personal.values.contains { $0.dirty } || outcomes.values.contains { !$0.outcome.isEmpty } }
    var visible: [MapItem] {
        let found = maps.filter { map in
            let text = [map.name, map.packageName, map.metadata.text("author"), map.metadata.text("description")].joined(separator: " ")
            return (query.isEmpty || text.localizedCaseInsensitiveContains(query)) &&
                (filter == "All maps" || (filter == "Known issue" ? map.verification == "Known issue" || ["amber", "red"].contains(map.summary.text("light")) : filter == "Verified" || filter == "Not verified" ? map.verification == filter : map.metadata.text("map_type").replacingOccurrences(of: " ", with: "").localizedCaseInsensitiveContains(filter.replacingOccurrences(of: " ", with: ""))))
        }
        return found.sorted { a, b in
            if sort == "Name Z–A" { return a.name.localizedStandardCompare(b.name) == .orderedDescending }
            if sort == "Author" {
                let aa = a.metadata.text("author", "~"), bb = b.metadata.text("author", "~")
                if aa != bb { return aa.localizedStandardCompare(bb) == .orderedAscending }
            }
            if sort == "Created newest" || sort == "Updated newest" {
                let key = sort == "Created newest" ? "created" : "updated"
                let aa = a.metadata.text(key), bb = b.metadata.text(key)
                if aa != bb { return aa > bb }
            }
            return a.name.localizedStandardCompare(b.name) == .orderedAscending
        }
    }

    init(fixture: Bool, backend: Backend? = nil) {
        self.fixture = fixture
        self.backend = backend ?? Backend()
        self.backend.event = { [weak self] name, data in self?.event(name, data) }
        self.backend.stopped = { [weak self] message in
            guard let self, !self.quitting else { return }
            self.connected = false; self.error = message; self.busy = false
        }
    }

    func start() async {
        guard !started else { return }
        started = true
        if fixture {
            do { maps = try Self.fixtureMaps().map(MapItem.init) } catch { self.error = error.localizedDescription }
            loaded = true; available = true; enrolled = true; connected = true
            selection = maps.first?.id; status = "Isolated synthetic fixture · no backend, file writes or game actions"
            preferences = ["language": "en", "voice": "", "automatic_updates": false, "crash_enabled": false, "crash_automatic": false, "resource_protection": true, "languages": [["id": "en", "name": "English"]]]
            if let view = Self.argument("--fixture-view") {
                if ["Preferences", "Collection", "Activity", "Original Game"].contains(view) { destination = view }
                else { section = view; narrowDetail = true }
            }
            if CommandLine.arguments.contains("--fixture-empty") { maps = []; selection = nil }
            if CommandLine.arguments.contains("--fixture-error") { error = "Synthetic error: last usable library retained. Inspect before retrying an unfinished operation." }
            if let map = selected {
                await loadDetails(map.id)
                if section == "Edit Details" { edit(map) }
                if section == "Record Outcome" { record(map); outcomes[map.id]?.outcome = "Problem" }
            }
            return
        }
        do { try backend.start(); connected = true; await refresh(); await loadPreferences() }
        catch { self.error = error.localizedDescription; loaded = true }
    }

    private func event(_ name: String, _ data: [String: Any]) {
        if name == "closed" { quitting = true; NotificationCenter.default.post(name: .nativeServicesClosed, object: nil) }
        else if name == "close.requested" { NSApplication.shared.terminate(nil) }
        else if name == "close.cancelled" { quitting = false }
        else if name == "history.exit" {
            exitPrompt = data
            Task { await refresh() }
        }
        else {
            if !data.text("status").isEmpty { status = data.text("status") }
            if !data.text("crash_status").isEmpty { crashStatus = data.text("crash_status") }
            if let active = data["busy"] as? Bool {
                serviceBusy = active
            }
            if (name == "ready" && loaded) || name == "library.changed" { pendingRefresh = true }
            if pendingRefresh && !busy && !serviceBusy {
                pendingRefresh = false
                Task { await refresh() }
            }
        }
    }

    func request(_ method: String, _ params: [String: Any] = [:], success: (([String: Any]) async -> Void)? = nil) async {
        while busy {
            do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return }
        }
        guard !Task.isCancelled else { return }
        error = ""
        if fixture {
            status = "Synthetic fixture: this action is disabled. No launcher service runs."
            return
        }
        busy = true
        do { let value = try await backend.call(method, params); await success?(value) }
        catch { self.error = error.localizedDescription }
        busy = false
        if pendingRefresh && !serviceBusy {
            pendingRefresh = false
            Task { await refresh() }
        }
    }

    func refresh() async {
        await request("snapshot", ["refresh": true]) { value in
            do {
                let replacement = try await Task.detached(priority: .userInitiated) {
                    try value.rows("maps").map(MapItem.init)
                }.value
                guard Set(replacement.map(\.id)).count == replacement.count else { throw NativeError.message("The library contains duplicate identities. Existing presentation retained.") }
                let old = Dictionary(uniqueKeysWithValues: self.maps.map { ($0.id, $0.archive + $0.variant) })
                for map in replacement where old[map.id] != nil && old[map.id] != map.archive + map.variant {
                    self.personal[map.id] = nil; self.outcomes[map.id] = nil
                }
                self.maps = replacement
                if !replacement.contains(where: { $0.id == self.selection }) { self.selection = replacement.first?.id }
                self.available = value.flag("available"); self.enrolled = value.flag("enrolled")
                self.status = value.text("status"); self.crashStatus = value.text("crash_status")
                self.loaded = true; self.details.removeAll()
            } catch { self.error = error.localizedDescription; self.loaded = true }
        }
        loaded = true
        if let id = selection, connected && !fixture { await loadDetails(id) }
    }

    func select(_ map: MapItem) {
        selection = map.id; narrowDetail = true
    }
    func loadDetails(_ id: String, force: Bool = false) async {
        if details[id] != nil && !force { return }
        if fixture {
            details[id] = ["scenarios": ["Coastal scenario"], "history": [
                ["kind": "process_confirmed", "label": "Process observed · synthetic", "attempt_id": "fictional-attempt", "scenario": "Coastal scenario", "game_id": "", "at": "2026-10-11 12:00", "source": "synthetic fixture"],
                ["kind": "crash_unknown", "label": "Crash · phase unknown · synthetic", "attempt_id": "fictional-attempt", "scenario": "Coastal scenario", "incident_id": "fictional-incident", "at": "2026-10-11 12:05", "source": "synthetic fixture"]
            ], "context": ["game_executable_sha256": String(repeating: "f", count: 64)], "verification_text": "Synthetic fixture · actual compatibility is unknown", "checks_text": "No package scan executed."]
            return
        }
        await request("details", ["profile_id": id]) { value in self.details[id] = value }
    }
    func memory(_ key: String) -> ScrollMemory {
        if let memory = detailScroll[key] { return memory }
        let memory = ScrollMemory(); detailScroll[key] = memory; return memory
    }
    func edit(_ map: MapItem) {
        if personal[map.id] == nil { personal[map.id] = PersonalDraft(title: map.title, notes: map.notes) }
        section = "Edit Details"; narrowDetail = true
    }
    func savePersonal(_ map: MapItem) async {
        guard let draft = personal[map.id] else { return }
        var params: [String: Any] = ["profile_id": map.id, "archive_sha256": map.archive, "variant_id": map.variant, "title": draft.title, "notes": draft.notes]
        if let image = draft.imagePNG { params["image_png"] = image }
        await request("annotations.save", params) { _ in
            self.personal[map.id] = nil; self.section = "Overview"; self.status = "Personal details saved. Original package preserved."
        }
        if error.isEmpty && !fixture { await refresh() }
    }
    func record(_ map: MapItem) {
        if outcomes[map.id] == nil {
            var draft = OutcomeDraft()
            let rows = details[map.id]?.rows("history") ?? []
            let opens = rows.filter { ["process_confirmed", "launch_attempted"].contains($0.text("kind")) }
            if let last = opens.last { draft.attempt = last.text("attempt_id"); draft.scenario = last.text("scenario") }
            outcomes[map.id] = draft
        }
        section = "Record Outcome"; narrowDetail = true
    }
    func openExitPrompt(_ prompt: [String: Any]) async {
        await refresh()
        guard let map = maps.first(where: { $0.id == prompt.text("profile_id") && $0.archive == prompt.text("archive_sha256") && $0.variant == prompt.text("variant_id") }) else {
            error = "This exited session belongs to a different library edition. Refresh and inspect its history before recording."; return
        }
        await loadDetails(map.id, force: true)
        guard details[map.id]?.object("context").text("game_executable_sha256") == prompt.text("game_executable_sha256") else {
            error = "This session belongs to a different executable build. Its outcome remains unknown."; return
        }
        select(map); record(map)
        outcomes[map.id]?.attempt = prompt.text("attempt_id")
        outcomes[map.id]?.scenario = prompt.text("scenario")
        destination = "Map Library"
    }
    func confirm(_ map: MapItem) async {
        guard let draft = outcomes[map.id] else { return }
        let kind: String
        switch draft.outcome {
        case "Played": kind = "playable"
        case "Problem": kind = draft.problem
        case "Saved": kind = "save_made"
        case "Resumed": kind = "save_reopened"
        case "Completed": kind = draft.reason
        default: error = "Choose the actual outcome."; return
        }
        guard !kind.isEmpty, !draft.attempt.isEmpty else { error = "Choose a recorded launch session and the required outcome details."; return }
        if ["Saved", "Resumed"].contains(draft.outcome) && draft.save.range(of: "^[0-9a-f]{64}$", options: .regularExpression) == nil {
            error = "Choose the exact save before confirming this outcome. The draft is retained."; return
        }
        var params: [String: Any] = ["profile_id": map.id, "archive_sha256": map.archive, "variant_id": map.variant, "game_executable_sha256": details[map.id]?.object("context").text("game_executable_sha256") ?? "", "attempt_id": draft.attempt, "kind": kind, "scenario": draft.scenario]
        if draft.outcome == "Problem" { params["failure_id"] = draft.incident; params["checkpoint"] = draft.checkpoint }
        else { params["game_id"] = draft.game }
        if ["Saved", "Resumed"].contains(draft.outcome) { params["save_id"] = draft.save }
        if draft.outcome == "Resumed" { params["continued"] = draft.continued }
        await request("history.record", params) { _ in
            self.outcomes[map.id] = nil; self.section = "Play History"; self.status = "Player confirmation recorded. Earlier failures remain visible."
        }
        if error.isEmpty && !fixture { await refresh(); await loadDetails(map.id, force: true) }
    }
    func workflow(_ action: String, profile: String? = nil, extras: [String: Any] = [:]) async {
        var params = extras; params["action"] = action
        if let profile { params["profile_id"] = profile }
        await request("workflow", params)
    }
    func quit() async {
        if fixture || !connected { NSApplication.shared.terminate(nil); return }
        await workflow("quit")
    }
    func loadPreferences() async {
        await request("prefs") { self.preferences = $0 }
    }
    func savePreference(_ key: String, _ value: Any) async {
        await request("prefs.save", [key: value]) { self.preferences = $0 }
    }
    func loadActivity() async { await request("activity") { self.activity = $0.rows("records") } }
    func loadCollection(refresh: Bool = false) async {
        await request("collection", ["refresh": refresh]) { value in self.collection = value.rows("records"); self.collectionNotice = value.text("notice") }
    }

    func translated(_ key: String) -> String { preferences.object("strings").text(key, key) }
    static func argument(_ name: String) -> String? {
        guard let index = CommandLine.arguments.firstIndex(of: name), CommandLine.arguments.indices.contains(index + 1) else { return nil }
        return CommandLine.arguments[index + 1]
    }

    private static func fixtureMaps() -> [[String: Any]] {
        ["Cedar Valley", "Highland Freight", "Lake District", "North Coast", "The Very Long Name of a Fictional Railway Across the Eastern Counties", "Unnamed Map"].enumerated().map { i, name in
            ["id": "synthetic-\(i)", "name": name, "archive_sha256": String(repeating: "a", count: 64), "variant_id": String(repeating: String(i + 1), count: 64),
             "metadata": ["author": i % 2 == 0 ? "Example author" : "", "description": "A fictional railway connecting inland towns with the coast. Synthetic content only.", "map_type": "Single player", "raw_text": "Synthetic package text. No game asset was read.", "source_urls": []],
             "verification": "Not verified", "notice": "", "art": fixtureArtwork(i), "history_error": "", "annotation": ["title": "", "notes": "", "image_png": ""],
             "summary": ["label": i == 0 ? "Known issue · fix not tested" : "Not verified", "light": i == 0 ? "amber" : "grey", "opens": 0, "started": 0, "completed": 0, "crashes": 0, "last": "Not recorded", "issue": "Not linked", "fix": "Not recorded", "test": "Not tested"]]
        }
    }

    private static func fixtureArtwork(_ index: Int) -> String {
        let image = NSImage(size: NSSize(width: 360, height: 240))
        image.lockFocus()
        NSColor(calibratedHue: CGFloat(index) / 9 + 0.08, saturation: 0.25, brightness: 0.8, alpha: 1).setFill()
        NSRect(x: 0, y: 0, width: 360, height: 240).fill()
        NSColor(calibratedWhite: 1, alpha: 0.35).setStroke()
        let railway = NSBezierPath(); railway.move(to: NSPoint(x: 20, y: 35))
        railway.curve(to: NSPoint(x: 340, y: 210), controlPoint1: NSPoint(x: 270, y: 15), controlPoint2: NSPoint(x: 80, y: 225))
        railway.lineWidth = 12; railway.stroke()
        for x in [60, 180, 290] {
            NSColor.white.setFill(); NSBezierPath(ovalIn: NSRect(x: x, y: 80 + (x % 3) * 32, width: 10, height: 10)).fill()
        }
        ("SYNTHETIC ILLUSTRATION" as NSString).draw(at: NSPoint(x: 16, y: 16), withAttributes: [.font: NSFont.systemFont(ofSize: 11, weight: .semibold), .foregroundColor: NSColor.black.withAlphaComponent(0.65)])
        image.unlockFocus()
        guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) else { return "" }
        return png.base64EncodedString()
    }
}
