import AppKit
import Combine
import Foundation
import ImageIO

struct MapItem: Identifiable {
    let id: String
    let packageName: String
    let archive: String
    let variant: String
    let build: String
    let metadata: [String: Any]
    var verification: String
    var notice: String
    var summary: [String: Any]
    let historyError: String
    var title: String
    var notes: String
    var imagePNG: String
    var artworkState: String
    var artworkMessage: String
    var artworkBytes = 0
    var packageImage: NSImage?
    var image: NSImage?
    var searchText: String
    var name: String { title.isEmpty ? packageName : title }
    var status: String {
        let history = summary.text("label", verification)
        if verification == "Known issue" && !history.hasPrefix("Known issue") { return "Known issue · " + history }
        if !historyError.isEmpty { return "History needs inspection · " + history }
        if verification == "Needs inspection" || !notice.isEmpty { return "Needs inspection · " + history }
        if verification == "Checks pending" { return history + " · Checks pending" }
        return history
    }
    var warning: Bool { verification == "Known issue" || ["amber", "red"].contains(summary.text("light")) || !historyError.isEmpty || !notice.isEmpty }

    init(_ data: [String: Any]) throws {
        guard !data.text("id").isEmpty, !data.text("archive_sha256").isEmpty, !data.text("variant_id").isEmpty else {
            throw NativeError.message("The map presentation has no stable identity. Refresh the library before using it.")
        }
        id = data.text("id"); packageName = data.text("name")
        archive = data.text("archive_sha256"); variant = data.text("variant_id")
        build = data.text("game_executable_sha256")
        metadata = data.object("metadata"); verification = data.text("verification", "Not verified")
        notice = data.text("notice"); summary = data.object("summary"); historyError = data.text("history_error")
        let annotation = data.object("annotation")
        title = annotation.text("title"); notes = annotation.text("notes"); imagePNG = annotation.text("image_png")
        artworkState = data.text("artwork_state", "ready"); artworkMessage = data.text("artwork_message")
        let packageArt = data.text("art")
        artworkBytes = packageArt.utf8.count + imagePNG.utf8.count
        packageImage = packageArt.utf8.count <= 1_400_000 ? Data(base64Encoded: packageArt).flatMap(NSImage.init(data:)) : nil
        image = imagePNG.isEmpty ? packageImage : Self.decodeImage(imagePNG)
        searchText = [title.isEmpty ? packageName : title, packageName, metadata.text("author"), metadata.text("description")].joined(separator: " ")
    }
    static func decodeImage(_ encoded: String) -> NSImage? {
        encoded.utf8.count <= 1_400_000 ? Data(base64Encoded: encoded).flatMap(NSImage.init(data:)) : nil
    }
    mutating func applyAnnotation(_ annotation: [String: Any], preparedImage: NSImage?, packageImage: NSImage?, notice: String? = nil) {
        self.packageImage = packageImage
        if let notice { self.notice = notice }
        title = annotation.text("title"); notes = annotation.text("notes"); imagePNG = annotation.text("image_png")
        image = imagePNG.isEmpty ? packageImage : preparedImage
        searchText = [name, packageName, metadata.text("author"), metadata.text("description")].joined(separator: " ")
    }
}

// Construct once off the main actor, then hand ownership to presentation.
// This value follows the existing MapItem transfer pattern on macOS 13.
struct PreparedArtwork {
    let png: String
    let image: NSImage?
    init(_ png: String) { self.png = png; self.image = MapItem.decodeImage(png) }
}

struct NativeToolConsent {
    let action: String
    let params: [String: Any]
}

struct PersonalDraft {
    let identity = UUID()
    var title: String
    var notes: String
    var imagePNG: String?
    var image: NSImage?
    var dirty = false
}
struct OutcomeDraft: Equatable {
    let identity = UUID()
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
struct OutcomeSaveChoice {
    let token = UUID()
    let key: String
    let draft: OutcomeDraft
}
struct BriefingTranslation {
    let archive: String
    let variant: String
    var source = "en"
    var target: String
    var text: String?
    var showingResult = false
}
final class ScrollMemory { var point: NSPoint? }

@MainActor final class LauncherModel: ObservableObject {
    @Published var maps: [MapItem] = [] { didSet { presentationRevision += 1 } }
    @Published var selection: String? {
        didSet { if oldValue != selection { outcomeSaveChoices.removeAll() } }
    }
    @Published var destination = "Map Library"
    @Published var section = "Overview"
    @Published var query = ""
    @Published var filter = "All maps"
    @Published var sort = "Name A–Z"
    @Published var status = "Opening your library…"
    @Published var crashStatus = ""
    @Published var error = ""
    @Published var busy = false
    @Published private(set) var reading = 0
    @Published private(set) var serviceBusy = false
    @Published var loaded = false
    @Published var available = false
    @Published var enrolled = false
    @Published var connected = false
    @Published var details: [String: [String: Any]] = [:]
    @Published var personal: [String: PersonalDraft] = [:]
    @Published var outcomes: [String: OutcomeDraft] = [:]
    @Published var outcomeAttempts: [String: String] = [:]
    @Published var outcomeUnknown = Set<String>()
    private var recordedReports = Set<String>()
    private var outcomeSaveChoices: [String: UUID] = [:]
    @Published var translations: [String: BriefingTranslation] = [:]
    @Published var mapToolDraftsPresent = false
    @Published var preferences: [String: Any] = [:]
    @Published var tools: [String: Any] = [:]
    @Published var preferenceCategory = "General"
    @Published var remoteSelection = Set<String>()
    private var toolPolling = false
    @Published var activity: [[String: Any]] = []
    @Published var collection: [[String: Any]] = []
    @Published var collectionNotice = ""
    @Published var collectionQuery = ""
    @Published var selectedRemote: String?
    @Published var narrowDetail = false
    @Published var exitPrompt: [String: Any]?
    let fixture: Bool
    let playDeadline: Double?
    private let playDeadlineError: String?
    let gridScroll = ScrollMemory()
    var detailScroll: [String: ScrollMemory] = [:]
    private let backend: Backend
    private var preferenceRevision = 0
    private var quitting = false
    private var started = false
    private var pendingRefresh = false
    private var presentationRevision = 0
    @Published private(set) var libraryRevision = 0
    private var refreshTask: Task<Void, Never>?
    private var visibleKey: (revision: Int, query: String, filter: String, sort: String)?
    private var visibleRows: [MapItem] = []
    private(set) var visibleBuilds = 0
    @Published private(set) var imageReads: [String: UUID] = [:]
    private var imageChoiceRevisions: [String: Int] = [:]
    private var artworkReads: [String: UUID] = [:]
    private var artworkOrder: [String] = []
    private var artworkVersions: [String: UUID] = [:]
    private var artworkActive = 0
    private var detailReads: [String: (token: UUID, revision: Int, archive: String, variant: String, build: String, task: Task<Void, Never>)] = [:]
    var selected: MapItem? { maps.first { $0.id == selection } }
    var working: Bool { busy || serviceBusy }
    var dirty: Bool { personal.values.contains { $0.dirty } || outcomes.values.contains { !$0.outcome.isEmpty } || mapToolDraftsPresent }
    var visible: [MapItem] {
        if let key = visibleKey, key.revision == presentationRevision,
           key.query == query, key.filter == filter, key.sort == sort { return visibleRows }
        visibleBuilds += 1
        let found = maps.filter { map in
            return (query.isEmpty || map.searchText.localizedCaseInsensitiveContains(query)) &&
                (filter == "All maps" || (filter == "Known issue" ? map.verification == "Known issue" || ["amber", "red"].contains(map.summary.text("light")) : filter == "Verified" || filter == "Not verified" ? map.verification == filter || map.verification == "Checks pending" : map.metadata.text("map_type").replacingOccurrences(of: " ", with: "").localizedCaseInsensitiveContains(filter.replacingOccurrences(of: " ", with: ""))))
        }
        visibleRows = found.sorted { a, b in
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
        visibleKey = (presentationRevision, query, filter, sort)
        return visibleRows
    }

    init(fixture: Bool, backend: Backend? = nil, arguments: [String] = CommandLine.arguments) {
        self.fixture = fixture
        let flags = arguments.indices.filter { arguments[$0] == "--play-deadline" }
        if let index = flags.first {
            if flags.count == 1, index + 1 < arguments.count,
               let deadline = Double(arguments[index + 1]), deadline.isFinite, deadline > 0 {
                playDeadline = deadline; playDeadlineError = nil
            } else {
                playDeadline = nil
                playDeadlineError = "--play-deadline requires one finite positive monotonic deadline."
            }
        } else { playDeadline = nil; playDeadlineError = nil }
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
        if let playDeadlineError { error = playDeadlineError; loaded = true; return }
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
            if CommandLine.arguments.contains("--fixture-loading") { maps = []; selection = nil; loaded = false; busy = true; status = "Synthetic initial loading; no work runs." }
            if CommandLine.arguments.contains("--fixture-refreshing") { busy = true; status = "Synthetic refresh; last usable maps retained. No work runs." }
            if CommandLine.arguments.contains("--fixture-error") { error = "Synthetic error: last usable library retained. Inspect before retrying an unfinished operation." }
            if let map = selected {
                await loadDetails(map.id)
                if section == "Edit Details" { edit(map) }
                if section == "Record Outcome" { record(map); updateOutcome(map) { $0.outcome = "Problem" } }
            }
            return
        }
        do { try backend.start(); connected = true; await refresh(); await loadPreferences() }
        catch { self.error = error.localizedDescription; loaded = true }
    }

    private func event(_ name: String, _ data: [String: Any]) {
        if name == "closed" { quitting = true; NotificationCenter.default.post(name: .nativeServicesClosed, object: nil) }
        else if name == "tools.navigate" {
            preferenceCategory = data.text("category", "General"); destination = data.text("destination", "Preferences")
        }
        else if name == "tools.changed" {
            tools = data
            if toolRunning { Task { await self.watchTools() } }
        }
        else if name == "tools.alert" { error = data.text("message") }
        else if name == "close.requested" { NSApplication.shared.terminate(nil) }
        else if name == "close.cancelled" { quitting = false; if !data.text("error").isEmpty { error = data.text("error") } }
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
            if pendingRefresh && !busy && !serviceBusy && refreshTask == nil {
                pendingRefresh = false
                Task { await refresh() }
            }
        }
    }

    func request(_ method: String, _ params: [String: Any] = [:], success: (([String: Any]) async -> Void)? = nil) async {
        let readOnly = ["details", "artwork", "prefs", "activity", "collection"].contains(method)
        while busy {
            do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return }
        }
        guard !Task.isCancelled else { return }
        error = ""
        if fixture {
            status = "Synthetic fixture: this action is disabled. No launcher service runs."
            return
        }
        if readOnly { reading += 1 } else { busy = true }
        do { let value = try await backend.call(method, params); await success?(value) }
        catch { self.error = error.localizedDescription }
        if readOnly { reading -= 1 } else { busy = false }
        if pendingRefresh && !serviceBusy && refreshTask == nil {
            pendingRefresh = false
            Task { await refresh() }
        }
    }

    func refresh() async {
        if let task = refreshTask { await task.value; return }
        let task = Task { await self.refreshLibrary() }
        refreshTask = task
        await task.value
        refreshTask = nil
        if pendingRefresh && !working {
            pendingRefresh = false
            await refresh()
        }
    }
    private func refreshLibrary() async {
        var replaced = false
        await request("snapshot", ["refresh": true]) { value in
            do {
                let replacement = try await Task.detached(priority: .userInitiated) {
                    try value.rows("maps").map(MapItem.init)
                }.value
                guard Set(replacement.map(\.id)).count == replacement.count else { throw NativeError.message("The library contains duplicate identities. Existing presentation retained.") }
                let old = Dictionary(uniqueKeysWithValues: self.maps.map { ($0.id, $0.archive + $0.variant) })
                for map in replacement where old[map.id] != nil && old[map.id] != map.archive + map.variant {
                    self.personal[map.id] = nil; self.translations[map.id] = nil
                }
                self.libraryRevision += 1
                self.artworkOrder.removeAll()
                self.maps = replacement
                if !replacement.contains(where: { $0.id == self.selection }) { self.selection = replacement.first?.id }
                self.available = value.flag("available"); self.enrolled = value.flag("enrolled")
                self.status = value.text("status"); self.crashStatus = value.text("crash_status")
                self.loaded = true; self.details.removeAll(); replaced = true
            } catch { self.error = error.localizedDescription; self.loaded = true }
        }
        loaded = true
        if replaced, let id = selection, connected && !fixture { await loadDetails(id) }
    }

    var pendingVerificationFilter: Bool {
        ["Verified", "Not verified"].contains(filter) && maps.contains { $0.verification == "Checks pending" }
    }

    func select(_ map: MapItem) {
        selection = map.id; narrowDetail = true
    }
    func translation(_ map: MapItem) -> BriefingTranslation {
        if let saved = translations[map.id], saved.archive == map.archive, saved.variant == map.variant { return saved }
        return BriefingTranslation(archive: map.archive, variant: map.variant, target: preferences.text("language", "en"))
    }
    func setTranslationLanguage(_ map: MapItem, _ key: WritableKeyPath<BriefingTranslation, String>, _ value: String) {
        var draft = translation(map)
        draft[keyPath: key] = value; draft.text = nil; draft.showingResult = false
        translations[map.id] = draft
    }
    func translateBriefing(_ map: MapItem) async {
        let draft = translation(map)
        await request("briefing.translate", ["profile_id": map.id, "archive_sha256": map.archive, "variant_id": map.variant, "source": draft.source, "target": draft.target]) { value in
            guard let current = self.maps.first(where: { $0.id == map.id }),
                  current.archive == map.archive, current.variant == map.variant,
                  value.text("profile_id") == map.id, value.text("archive_sha256") == map.archive,
                  value.text("variant_id") == map.variant, value.text("source") == draft.source,
                  value.text("target") == draft.target, let text = value["text"] as? String else {
                self.error = "The briefing translation no longer matches this map edition. Original briefing retained."
                return
            }
            var result = self.translation(current)
            guard result.source == draft.source, result.target == draft.target else { return }
            result.text = text; result.showingResult = true
            self.translations[map.id] = result
            self.status = "Briefing translated. Original package preserved."
        }
    }
    func loadDetails(_ id: String, force: Bool = false) async {
        if details[id] != nil && !force { return }
        if fixture {
            details[id] = ["scenarios": ["Coastal scenario"], "history": [
                ["kind": "process_confirmed", "label": "Process observed · synthetic", "attempt_id": "fictional-attempt", "scenario": "Coastal scenario", "game_id": "", "at": "2026-10-11 12:00", "source": "synthetic fixture"],
                ["kind": "crash_unknown", "label": "Crash · phase unknown · synthetic", "attempt_id": "fictional-attempt", "scenario": "Coastal scenario", "incident_id": "fictional-incident", "at": "2026-10-11 12:05", "source": "synthetic fixture"]
            ], "context": ["game_executable_sha256": String(repeating: "f", count: 64)], "languages": [["id": "en", "name": "English"], ["id": "fr", "name": "Français"]], "verification_text": "Synthetic fixture · actual compatibility is unknown", "checks_text": "No package scan executed."]
            return
        }
        guard let map = maps.first(where: { $0.id == id }) else { return }
        let revision = libraryRevision
        if !force, let active = detailReads[id], active.revision == revision,
           active.archive == map.archive, active.variant == map.variant, active.build == map.build {
            await active.task.value
            return
        }
        let token = UUID()
        let wasSelected = selection == id
        let task = Task { @MainActor in
            defer { if self.detailReads[id]?.token == token { self.detailReads[id] = nil } }
            // Selection can change several times within one UI update. Do not
            // submit a cancelled/superseded selection's read to the service.
            if !force { try? await Task.sleep(nanoseconds: 30_000_000) }
            guard self.detailReads[id]?.token == token,
                  force || !wasSelected || self.selection == id else { return }
            await self.request("details", ["profile_id": id]) { value in
                let context = value.object("context")
                guard self.detailReads[id]?.token == token, self.libraryRevision == revision else { return }
                guard value.text("profile_id") == id,
                      let index = self.maps.firstIndex(where: {
                          $0.id == id && $0.archive == context.text("archive_sha256") && $0.variant == context.text("variant_id") &&
                          $0.archive == map.archive && $0.variant == map.variant &&
                          !map.build.isEmpty && $0.build == map.build && $0.build == context.text("game_executable_sha256")
                      }) else {
                    self.error = "The detail response no longer matches this map edition or executable build. Last presentation retained; refresh before continuing."
                    return
                }
                self.details[id] = value
                if let verification = value["verification"] as? String { self.maps[index].verification = verification }
                if let summary = value["summary"] as? [String: Any] { self.maps[index].summary = summary }
            }
        }
        detailReads[id] = (token, revision, map.archive, map.variant, map.build, task)
        await task.value
    }
    func loadArtwork(_ id: String) async {
        guard !fixture else { return }
        while artworkActive >= 2 || busy || artworkReads[id] != nil {
            do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return }
        }
        guard !Task.isCancelled, let map = maps.first(where: { $0.id == id }),
              map.artworkState == "pending", artworkReads[id] == nil else { return }
        let token = UUID(), revision = libraryRevision, version = artworkVersions[id]
        artworkReads[id] = token; artworkActive += 1
        defer { artworkActive -= 1; if artworkReads[id] == token { artworkReads[id] = nil } }
        do {
            let value = try await backend.call("artwork", ["profile_id": id, "archive_sha256": map.archive,
                "variant_id": map.variant, "game_executable_sha256": map.build])
            guard value.text("profile_id") == id, value.text("archive_sha256") == map.archive,
                  value.text("variant_id") == map.variant, value.text("game_executable_sha256") == map.build else {
                throw NativeError.message("The artwork response no longer matches this map edition or executable build.")
            }
            let package = value.text("art"), personalImage = value.text("image_png")
            let prepared = await Task.detached(priority: .utility) {
                (PreparedArtwork(package), PreparedArtwork(personalImage))
            }.value
            guard libraryRevision == revision, artworkReads[id] == token, artworkVersions[id] == version,
                  let index = maps.firstIndex(where: { $0.id == id && $0.archive == map.archive && $0.variant == map.variant && $0.build == map.build }) else { return }
            maps[index].packageImage = prepared.0.image; maps[index].imagePNG = personalImage
            maps[index].image = personalImage.isEmpty ? prepared.0.image : prepared.1.image
            maps[index].artworkBytes = package.utf8.count + personalImage.utf8.count
            maps[index].artworkState = value.text("artwork_state", "error")
            maps[index].artworkMessage = value.text("artwork_message")
            if (!package.isEmpty && prepared.0.image == nil) || (!personalImage.isEmpty && prepared.1.image == nil) {
                maps[index].artworkState = "error"; maps[index].artworkMessage = "Map artwork could not be decoded. Original artwork is unchanged."
            }
            retainArtwork(id)
        } catch {
            guard libraryRevision == revision, artworkVersions[id] == version,
                  let index = maps.firstIndex(where: { $0.id == id && $0.archive == map.archive && $0.variant == map.variant }) else { return }
            maps[index].artworkState = "error"; maps[index].artworkMessage = error.localizedDescription
        }
    }

    private func retainArtwork(_ id: String) {
        artworkOrder.removeAll { $0 == id }; artworkOrder.append(id)
        // Keep at most 32 MiB of encoded artwork (and 64 decoded map pairs).
        // Evicted cards fetch again when SwiftUI displays them; drafts are separate.
        while artworkOrder.count > 64 || maps.reduce(0, { $0 + $1.artworkBytes }) > 32 * 1024 * 1024 {
            guard let oldest = artworkOrder.first(where: { $0 != id && $0 != selection }) else { break }
            artworkOrder.removeAll { $0 == oldest }
            if let index = maps.firstIndex(where: { $0.id == oldest }) {
                maps[index].packageImage = nil; maps[index].image = nil; maps[index].imagePNG = ""
                maps[index].artworkBytes = 0; maps[index].artworkState = "pending"
            }
        }
    }

    func retryArtwork(_ id: String) async {
        guard let index = maps.firstIndex(where: { $0.id == id }), maps[index].artworkState == "error" else { return }
        maps[index].artworkState = "pending"; maps[index].artworkMessage = ""
        await loadArtwork(id)
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
        guard let draft = personal[map.id], imageReads[map.id] == nil else { return }
        var params: [String: Any] = ["profile_id": map.id, "archive_sha256": map.archive, "variant_id": map.variant, "title": draft.title, "notes": draft.notes]
        if let image = draft.imagePNG { params["image_png"] = image }
        artworkVersions[map.id] = UUID()
        await request("annotations.save", params) { value in
            guard value.flag("saved"), value.text("profile_id") == map.id, value.text("archive_sha256") == map.archive,
                  value.text("variant_id") == map.variant,
                  let index = self.maps.firstIndex(where: { $0.id == map.id && $0.archive == map.archive && $0.variant == map.variant }),
                  let annotation = value["annotation"] as? [String: Any],
                  let packageArt = value["art"] as? String else {
                self.error = "Personal details were saved, but their current presentation needs inspection. Refresh before continuing."
                return
            }
            let artwork = await Task.detached(priority: .userInitiated) {
                (PreparedArtwork(annotation.text("image_png")), PreparedArtwork(packageArt))
            }.value
            guard self.maps.indices.contains(index), self.maps[index].id == map.id,
                  self.maps[index].archive == map.archive, self.maps[index].variant == map.variant else { return }
            self.maps[index].applyAnnotation(annotation, preparedImage: artwork.0.image, packageImage: artwork.1.image, notice: value["notice"] as? String)
            self.maps[index].artworkBytes = annotation.text("image_png").utf8.count + packageArt.utf8.count
            self.maps[index].artworkState = "pending"
            if let current = self.personal[map.id], current.identity == draft.identity, current.title == draft.title,
               current.notes == draft.notes, current.imagePNG == draft.imagePNG,
               self.imageReads[map.id] == nil { self.personal[map.id] = nil }
            if self.selection == map.id && self.personal[map.id] == nil { self.section = "Overview" }
            self.status = "Personal details saved. Original package preserved."
        }
        // A save invalidates older image replies even when persistence fails.
        // Resume an unfinished image read after the mutation gate is released.
        Task { await self.loadArtwork(map.id) }
    }

    func reserveImageChoice(_ map: MapItem) -> Int {
        let revision = (imageChoiceRevisions[map.id] ?? 0) + 1
        imageChoiceRevisions[map.id] = revision
        return revision
    }
    func currentImageChoice(_ map: MapItem, _ revision: Int) -> Bool {
        imageChoiceRevisions[map.id] == revision
    }
    func usePackageImage(_ map: MapItem) {
        _ = reserveImageChoice(map)
        imageReads[map.id] = nil
        guard var draft = personal[map.id] else { return }
        draft.imagePNG = ""; draft.image = nil; draft.dirty = true; personal[map.id] = draft
    }
    func prepareImage(_ url: URL, for map: MapItem) async {
        _ = reserveImageChoice(map)
        guard let draftIdentity = personal[map.id]?.identity else { return }
        let token = UUID()
        imageReads[map.id] = token
        defer { if imageReads[map.id] == token { imageReads[map.id] = nil } }
        do {
            let prepared = try await Task.detached(priority: .userInitiated) { () -> PreparedArtwork in
                let file = try FileHandle(forReadingFrom: url); defer { try? file.close() }
                let data = try file.read(upToCount: 8 * 1024 * 1024 + 1) ?? Data()
                guard data.count <= 8 * 1024 * 1024, let source = CGImageSourceCreateWithData(data as CFData, nil),
                      let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 360, kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else {
                    throw NativeError.message("Image could not be read. Choose a supported image of at most 8 MiB; the previous image and draft are retained.")
                }
                let representation = NSBitmapImageRep(cgImage: thumbnail)
                guard let png = representation.representation(using: .png, properties: [:]), png.count <= 1024 * 1024 else {
                    throw NativeError.message("The prepared image exceeds 1 MiB. Previous image and draft retained.")
                }
                return PreparedArtwork(png.base64EncodedString())
            }.value
            guard imageReads[map.id] == token, maps.contains(where: { $0.id == map.id && $0.archive == map.archive && $0.variant == map.variant }), var draft = personal[map.id], draft.identity == draftIdentity else { return }
            draft.imagePNG = prepared.png; draft.image = prepared.image; draft.dirty = true; personal[map.id] = draft
        } catch { if imageReads[map.id] == token { self.error = error.localizedDescription } }
    }
    func reportBuild(_ map: MapItem) -> String {
        map.build.isEmpty ? details[map.id]?.object("context").text("game_executable_sha256") ?? "" : map.build
    }
    func reportScope(_ map: MapItem) -> String {
        [map.id, map.archive, map.variant, reportBuild(map)].joined(separator: ":")
    }
    func outcomeKey(_ map: MapItem) -> String { reportScope(map) + ":" + (outcomeAttempts[reportScope(map)] ?? "") }
    func outcome(_ map: MapItem) -> OutcomeDraft { outcomes[outcomeKey(map)] ?? OutcomeDraft() }
    func reportRows(_ map: MapItem) -> [[String: Any]] {
        let data = details[map.id] ?? [:], context = data.object("context")
        guard context.text("game_executable_sha256") == reportBuild(map),
              context.text("archive_sha256", map.archive) == map.archive,
              context.text("variant_id", map.variant) == map.variant else { return [] }
        return data.rows("history")
    }
    func reportGames(_ map: MapItem, _ draft: OutcomeDraft) -> [String] {
        Array(Set(reportRows(map).filter {
            ["game_started", "game_resumed"].contains($0.text("kind")) &&
            $0.text("attempt_id") == draft.attempt && $0.text("scenario") == draft.scenario
        }.map { $0.text("game_id") }.filter { !$0.isEmpty })).sorted()
    }
    func reportAvailableGames(_ map: MapItem, _ draft: OutcomeDraft) -> [String] {
        Array(Set(reportRows(map).filter {
            ["game_started", "game_resumed"].contains($0.text("kind")) && $0.text("scenario") == draft.scenario
        }.map { $0.text("game_id") }.filter { !$0.isEmpty })).sorted()
    }
    func updateOutcome(_ map: MapItem, _ change: (inout OutcomeDraft) -> Void) {
        let key = outcomeKey(map)
        guard !outcomeUnknown.contains(key) else { return }
        var draft = outcome(map)
        let previous = draft
        change(&draft)
        if previous != draft { outcomeSaveChoices[key] = nil }
        outcomes[key] = draft
    }
    func beginOutcomeSaveChoice(_ map: MapItem) -> OutcomeSaveChoice {
        let choice = OutcomeSaveChoice(key: outcomeKey(map), draft: outcome(map))
        outcomeSaveChoices[choice.key] = choice.token
        return choice
    }
    func acceptOutcomeSaveChoice(_ map: MapItem, _ choice: OutcomeSaveChoice, _ save: String) {
        guard let selected, reportScope(selected) == reportScope(map),
              outcomeKey(map) == choice.key, outcomeSaveChoices[choice.key] == choice.token,
              outcome(map) == choice.draft, !outcomeUnknown.contains(choice.key),
              ["Saved", "Resumed"].contains(choice.draft.outcome) else { return }
        outcomeSaveChoices[choice.key] = nil
        guard save.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else {
            error = "The selected save identity is invalid. The report draft is retained."; return
        }
        updateOutcome(map) { $0.save = save }
    }
    func setOutcomeSave(_ map: MapItem, _ save: String) {
        outcomeSaveChoices[outcomeKey(map)] = nil
        updateOutcome(map) { $0.save = save }
    }
    func setOutcomeScenario(_ map: MapItem, _ scenario: String) {
        updateOutcome(map) { draft in
            if draft.scenario != scenario {
                draft.scenario = scenario; draft.game = ""; draft.incident = ""; draft.save = ""; draft.checkpoint = ""
            }
        }
        reuseReportContext(map, inferScenario: false)
    }
    func reuseReportContext(_ map: MapItem, inferScenario: Bool = true) {
        let key = outcomeKey(map)
        guard var draft = outcomes[key], !outcomeUnknown.contains(key) else { return }
        let rows = reportRows(map).filter { $0.text("attempt_id") == draft.attempt }
        let scenarios = Set(rows.map { $0.text("scenario") }.filter { !$0.isEmpty })
        if inferScenario && draft.scenario.isEmpty && scenarios.count == 1 { draft.scenario = scenarios.first! }
        let games = reportGames(map, draft)
        if draft.game.isEmpty && games.count == 1 { draft.game = games[0] }
        outcomes[key] = draft
    }
    func record(_ map: MapItem, attempt: String? = nil) {
        let rows = reportRows(map)
        let opens = rows.filter { ["process_confirmed", "launch_attempted"].contains($0.text("kind")) }
        let scope = reportScope(map)
        let selected = attempt ?? outcomeAttempts[scope] ?? opens.last?.text("attempt_id") ?? ""
        if let previous = outcomeAttempts[scope], previous != selected {
            outcomeSaveChoices[scope + ":" + previous] = nil
            outcomeSaveChoices[scope + ":" + selected] = nil
        }
        outcomeAttempts[scope] = selected
        let key = outcomeKey(map)
        if outcomes[key] == nil {
            var draft = OutcomeDraft(); draft.attempt = selected; outcomes[key] = draft
        }
        reuseReportContext(map)
        section = "Record Outcome"; narrowDetail = true
    }
    func chooseOutcome(_ map: MapItem, _ value: String) {
        updateOutcome(map) {
            if ["goals_met", "retired"].contains(value) { $0.outcome = "Completed"; $0.reason = value }
            else { $0.outcome = value }
            if value != "Resumed" { $0.continued = false }
        }
        if value == "Problem" {
            let draft = outcome(map)
            let failures = reportRows(map).filter {
                ["crash_load", "crash_play", "crash_unknown", "resource_stop"].contains($0.text("kind")) &&
                $0.text("attempt_id") == draft.attempt && ($0.text("scenario").isEmpty || $0.text("scenario") == draft.scenario)
            }
            let incidents = Set(failures.map { $0.text("incident_id") }.filter { !$0.isEmpty })
            if draft.incident.isEmpty && incidents.count == 1, let failure = failures.last {
                updateOutcome(map) { $0.incident = failure.text("incident_id"); $0.problem = failure.text("kind"); $0.checkpoint = failure.text("checkpoint") }
            }
        }
    }
    func reportKind(_ draft: OutcomeDraft) -> String {
        switch draft.outcome {
        case "Played": return "playable"
        case "Problem": return draft.problem
        case "Saved": return "save_made"
        case "Resumed": return "save_reopened"
        case "Completed": return draft.reason
        default: return ""
        }
    }
    private func reportFingerprint(_ key: String, _ draft: OutcomeDraft) -> String {
        // A session checkpoint has semantic identity independent of click time.
        [key, reportKind(draft), draft.scenario, draft.outcome == "Problem" ? "" : draft.game,
         ["Saved", "Resumed"].contains(draft.outcome) ? draft.save : "",
         draft.outcome == "Problem" ? draft.incident : "", draft.outcome == "Problem" ? draft.checkpoint : "",
         String(draft.outcome == "Resumed" && draft.continued)].joined(separator: "|")
    }
    func reportAlreadyRecorded(_ map: MapItem, _ draft: OutcomeDraft) -> Bool {
        // An explicitly new failure is a distinct incident, not a repeat of an
        // identified observation. Retained incidents are compared below.
        if draft.outcome == "Problem" && draft.incident.isEmpty { return false }
        if recordedReports.contains(reportFingerprint(outcomeKey(map), draft)) { return true }
        let kind = reportKind(draft)
        guard !kind.isEmpty else { return false }
        func matches(_ row: [String: Any], _ wanted: String) -> Bool {
            row.text("kind") == wanted && row.text("source") == "player_confirmed" &&
            row.text("attempt_id") == draft.attempt && row.text("scenario") == draft.scenario &&
            (draft.game.isEmpty || row.text("game_id") == draft.game) &&
            (!["Saved", "Resumed"].contains(draft.outcome) || row.text("save_id") == draft.save) &&
            (draft.outcome != "Problem" || (!draft.incident.isEmpty && row.text("incident_id") == draft.incident && row.text("checkpoint") == draft.checkpoint))
        }
        let rows = reportRows(map)
        return rows.contains { matches($0, kind) } && (draft.outcome != "Resumed" || !draft.continued || rows.contains { matches($0, "continued_play") })
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
        guard reportRows(map).contains(where: { $0.text("attempt_id") == prompt.text("attempt_id") && ["process_confirmed", "launch_attempted"].contains($0.text("kind")) }) else {
            error = "The exited session is no longer recorded in this exact scope. Inspect its history before reporting."; return
        }
        select(map); record(map, attempt: prompt.text("attempt_id"))
        destination = "Map Library"
    }
    func confirm(_ map: MapItem) async {
        let key = outcomeKey(map), build = reportBuild(map)
        guard let draft = outcomes[key], !outcomeUnknown.contains(key) else { return }
        while working {
            do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return }
        }
        // A preceding queued confirmation may have become UNKNOWN while this
        // submission waited. Never carry pre-wait permission through that hold.
        guard !outcomeUnknown.contains(key) else { return }
        guard !Task.isCancelled, outcomeKey(map) == key, outcomes[key] == draft,
              maps.contains(where: { $0.id == map.id && $0.archive == map.archive && $0.variant == map.variant && reportBuild($0) == build }),
              details[map.id]?.object("context").text("game_executable_sha256") == build else {
            error = "The report target or build changed. Its draft is retained; review the exact edition/session again."; return
        }
        let kind = reportKind(draft)
        guard !kind.isEmpty, !draft.attempt.isEmpty else { error = "Choose a recorded launch session and the required outcome details."; return }
        guard !reportAlreadyRecorded(map, draft) else { status = "This confirmation is already recorded. Choose another outcome; earlier observations are retained."; return }
        if ["Saved", "Resumed"].contains(draft.outcome) && draft.save.range(of: "^[0-9a-f]{64}$", options: .regularExpression) == nil {
            error = "Choose the exact save before confirming this outcome. The draft is retained."; return
        }
        var params: [String: Any] = ["profile_id": map.id, "archive_sha256": map.archive, "variant_id": map.variant, "game_executable_sha256": build, "attempt_id": draft.attempt, "kind": kind, "scenario": draft.scenario]
        if draft.outcome == "Problem" { params["failure_id"] = draft.incident; params["checkpoint"] = draft.checkpoint }
        else { params["game_id"] = draft.game }
        if ["Saved", "Resumed"].contains(draft.outcome) { params["save_id"] = draft.save }
        if draft.outcome == "Resumed" { params["continued"] = draft.continued }
        var received = false
        await request("history.record", params) { _ in
            received = true; self.recordedReports.insert(self.reportFingerprint(key, draft))
            // Preserve a newer edit made while the confirmation was in flight.
            if self.outcomes[key] == draft {
                var retained = self.outcomes[key]!
                retained.outcome = ""; retained.reason = ""; retained.continued = false; retained.incident = ""; retained.checkpoint = ""
                if retained.game == "new" { retained.game = "" }
                self.outcomes[key] = retained
            }
            if let selected = self.selected, self.reportScope(selected) == self.reportScope(map), self.outcomeKey(map) == key {
                self.status = "Player confirmation recorded. Session context retained; earlier failures remain visible."
            }
        }
        if !received && (!connected || error.hasPrefix("History outcome needs inspection")) { outcomeUnknown.insert(key) }
        // One targeted read, never a full gallery reset. Context survives read failure.
        if received && !fixture { await loadDetails(map.id, force: true); reuseReportContext(map) }
    }
    func workflow(_ action: String, profile: String? = nil, extras: [String: Any] = [:]) async {
        var params = extras; params["action"] = action
        if let profile { params["profile_id"] = profile }
        await request("workflow", params)
    }
    func quit() async {
        if fixture || !connected { NSApplication.shared.terminate(nil); return }
        await loadTools()
        var choice = ""
        if tools.flag("update_pending") {
            let alert = NSAlert()
            alert.messageText = "Install the verified update when quitting?"
            alert.informativeText = "The publisher and staged app will be checked again. Continue Working keeps your drafts and launcher open."
            alert.addButton(withTitle: "Continue Working")
            alert.addButton(withTitle: "Install & Quit")
            alert.addButton(withTitle: "Quit Without Installing")
            switch alert.runModal() {
            case .alertSecondButtonReturn: choice = "install"
            case .alertThirdButtonReturn: choice = "skip"
            default: return
            }
        }
        await workflow("quit", extras: ["update_choice": choice])
    }
    func loadPreferences(refreshVoices: Bool = false) async {
        let revision = preferenceRevision
        await request("prefs", refreshVoices ? ["refresh_voices": true] : [:]) { value in
            guard revision == self.preferenceRevision else { return }
            self.preferences = value
        }
    }
    func savePreference(_ key: String, _ value: Any) async {
        // Capture rollback state only after earlier mutations settle. It must
        // never be another request's unconfirmed optimistic value.
        while busy {
            do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return }
        }
        guard !Task.isCancelled else { return }
        let previous = preferences[key]
        preferenceRevision += 1
        let revision = preferenceRevision
        preferences[key] = value
        var saved = false
        await request("prefs.save", [key: value]) { reply in
            saved = true
            guard revision == self.preferenceRevision else { return }
            self.preferences = reply
        }
        if !saved && revision == preferenceRevision { preferences[key] = previous }
    }
    func loadActivity() async { await request("activity") { self.activity = $0.rows("records") } }
    func loadCollection(refresh: Bool = false) async {
        await request("collection", ["refresh": refresh]) { value in self.collection = value.rows("records"); self.collectionNotice = value.text("notice") }
    }

    var toolJob: [String: Any] { tools.object("job") }
    var toolRunning: Bool { toolJob.text("state") == "running" }
    // Only these read/cancel controls bypass the ordered mutation busy gate.
    func toolControl(_ method: String, _ params: [String: Any] = [:]) async -> [String: Any]? {
        guard ["tools.status", "tools.cancel"].contains(method), !fixture, connected else { return nil }
        do { return try await backend.call(method, params) }
        catch { self.error = error.localizedDescription; return nil }
    }
    func loadTools() async {
        if fixture {
            tools = ["version": "Synthetic", "available": true, "enrolled": true, "monitor_available": false, "crash_notice": "Synthetic fixture. No reports or services were read.", "job": [:]]
            return
        }
        if let value = await toolControl("tools.status") {
            tools = value
            if toolRunning { Task { await self.watchTools() } }
        }
    }
    func captureToolConsent(_ action: String, path: String = "") -> NativeToolConsent {
        var params: [String: Any] = ["consent": true]
        if action == "update.stage" {
            params["sha256"] = tools.text("update_sha256")
            params["version"] = tools.text("update_version")
            params["size"] = tools["update_size"] ?? 0
        }
        if action == "library.choose" { params["path"] = path }
        return NativeToolConsent(action: action, params: params)
    }
    func submitToolConsent(_ consent: NativeToolConsent) async {
        if consent.action == "update.stage" && (consent.params.text("sha256") != tools.text("update_sha256") || consent.params.text("version") != tools.text("update_version") || consent.params["size"] as? Int != tools["update_size"] as? Int) {
            error = "The available release changed after this confirmation opened. Review the current release before downloading."
            return
        }
        await runTool(consent.action, consent.params)
    }
    func runTool(_ action: String, _ params: [String: Any] = [:]) async {
        var values = params; values["action"] = action
        await request("tools.start", values) { value in
            self.tools["job"] = ["job_id": value.text("job_id"), "state": "running", "action": action, "message": "Working…"]
            Task { await self.watchTools() }
        }
    }
    private func watchTools() async {
        guard !toolPolling else { return }
        toolPolling = true
        defer { toolPolling = false }
        while connected && !fixture {
            let id = toolJob.text("job_id")
            guard !id.isEmpty, let value = await toolControl("tools.status") else { return }
            tools = value
            if !toolRunning {
                status = toolJob.text("message")
                if ["failed", "unknown"].contains(toolJob.text("state")) { error = status }
                return
            }
            do { try await Task.sleep(nanoseconds: 350_000_000) } catch { return }
        }
    }
    func cancelTool() async {
        let id = toolJob.text("job_id")
        if let value = await toolControl("tools.cancel", ["job_id": id]), value.flag("cancel_requested") {
            tools["job"] = toolJob.merging(["cancel_requested": true]) { _, new in new }
            status = "Cancellation requested. Waiting for the current safe boundary; completed imports remain."
        }
    }

    func translated(_ key: String) -> String { preferences.object("strings").text(key, key) }
    static func argument(_ name: String) -> String? {
        guard let index = CommandLine.arguments.firstIndex(of: name), CommandLine.arguments.indices.contains(index + 1) else { return nil }
        return CommandLine.arguments[index + 1]
    }

    private static func fixtureMaps() -> [[String: Any]] {
        ["Cedar Valley", "Highland Freight", "Lake District", "North Coast", "The Very Long Name of a Fictional Railway Across the Eastern Counties", "Unnamed Map"].enumerated().map { i, name in
            ["id": "synthetic-\(i)", "name": name, "archive_sha256": String(repeating: "a", count: 64), "variant_id": String(repeating: String(i + 1), count: 64), "game_executable_sha256": String(repeating: "f", count: 64),
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
