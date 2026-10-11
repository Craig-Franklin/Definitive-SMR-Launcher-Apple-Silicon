import AppKit
import ImageIO
import SwiftUI
import UniformTypeIdentifiers

// Select public wrappers explicitly for the Command Line Tools compiler.
private typealias ViewState<Value> = SwiftUI.State<Value>

struct LauncherView: View {
    @ObservedObject var model: LauncherModel
    @ViewState private var preference = "General"
    @ViewState private var discardMap: String?
    @ViewState private var showingDiscard = false
    @FocusState private var searchFocused: Bool
    private let destinations = ["Map Library", "Collection", "Original Game", "Activity", "Preferences"]

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                if geometry.size.width >= 1080 {
                    NavigationSplitView {
                        List(selection: $model.destination) {
                            ForEach(destinations, id: \.self) { name in
                                Label(model.translated(name), systemImage: symbol(name)).tag(name)
                            }
                        }.navigationSplitViewColumnWidth(min: 160, ideal: 180, max: 220)
                    } content: {
                        library.frame(minWidth: 350)
                            .navigationSplitViewColumnWidth(min: 350, ideal: 420, max: 650)
                            .navigationTitle(model.destination)
                    } detail: {
                        destination
                            .navigationSplitViewColumnWidth(min: 380, ideal: 480, max: 760)
                    }.navigationSplitViewStyle(.balanced)
                } else {
                    HStack {
                        Picker("Destination", selection: $model.destination) {
                            ForEach(destinations, id: \.self) { Text(model.translated($0)) }
                        }.labelsHidden().frame(width: 180)
                        Spacer()
                        if model.destination == "Map Library" && model.narrowDetail {
                            Button("Back to Library", systemImage: "chevron.left") { model.narrowDetail = false }
                        }
                    }.padding(12)
                    Divider()
                    if model.destination == "Map Library" && !model.narrowDetail { library }
                    else { destination }
                }
                Divider()
                statusBar
            }
            .toolbar {
                ToolbarItemGroup {
                    if model.destination == "Map Library" {
                        TextField(model.translated("Search maps"), text: $model.query).textFieldStyle(.roundedBorder)
                            .frame(minWidth: 150, idealWidth: 220, maxWidth: 300).focused($searchFocused)
                            .accessibilityLabel("Search maps")
                        Button("Import Map", systemImage: "square.and.arrow.down", action: importMap)
                            .disabled(!model.enrolled || model.working || !model.connected)
                    }
                    Menu {
                        Button("Refresh Library") { Task { await model.refresh() } }
                        Button("Choose Steam Library…", action: chooseLibrary)
                        Divider()
                        Button("Advanced Map Tools…") { advanced("details") }.disabled(model.selected == nil)
                        Button("Collection Tools…") { advanced("collection") }
                        Button("Installation & Recovery…") { advanced("setup") }
                        Button("Crash Reports…") { advanced("crash-reports") }
                        Button("Check for Updates…") { advanced("updates") }
                    } label: { Label("More", systemImage: "ellipsis.circle") }
                    .disabled(model.working || !model.connected)
                }
            }
            .onChange(of: model.destination) { destination in
                if destination == "Preferences" { Task { await model.loadPreferences() } }
                if destination == "Activity" { Task { await model.loadActivity() } }
                if destination == "Collection" { Task { await model.loadCollection() } }
            }
            .task(id: model.selection) {
                if let id = model.selection { await model.loadDetails(id) }
            }
            .confirmationDialog("Keep these personal changes?", isPresented: $showingDiscard, titleVisibility: .visible) {
                Button("Keep Draft & Close") { model.section = "Overview" }
                Button("Discard Draft", role: .destructive) {
                    if let id = discardMap { model.personal[id] = nil }
                    model.section = "Overview"
                }
                Button("Continue Editing", role: .cancel) { }
            } message: { Text("The draft stays with this map while the launcher is open. Saved package files remain unchanged.") }
            .alert("Record this session?", isPresented: Binding(get: { model.exitPrompt != nil }, set: { if !$0 { model.exitPrompt = nil } })) {
                Button("Record Outcome") {
                    if let prompt = model.exitPrompt { Task { await model.openExitPrompt(prompt) } }
                    model.exitPrompt = nil
                }
                Button("Later", role: .cancel) { model.exitPrompt = nil }
            } message: { Text("The game process exited. Its outcome stays unknown until you explicitly confirm what happened.") }
            .background {
                Button("Focus Search") { model.destination = "Map Library"; searchFocused = true }.keyboardShortcut("f").hidden()
                Button("Import Map", action: importMap).keyboardShortcut("o").hidden()
            }
            .onExitCommand {
                if model.section == "Edit Details", let map = model.selected, model.personal[map.id]?.dirty == true {
                    discardMap = map.id; showingDiscard = true
                } else { model.section = "Overview"; model.narrowDetail = false }
            }
        }
    }

    private var statusBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !model.error.isEmpty {
                HStack(alignment: .top) {
                    Label(model.error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).textSelection(.enabled)
                    Spacer()
                    Button("Dismiss") { model.error = "" }
                }
            }
            HStack {
                if model.working || !model.loaded { ProgressView().controlSize(.small) }
                Text(model.status).lineLimit(2).textSelection(.enabled)
                Spacer()
                Button(model.error.isEmpty ? "Refresh" : "Refresh Library") { Task { await model.refresh() } }
                    .disabled(model.working || !model.connected)
            }
        }.font(.caption).padding(.horizontal, 16).padding(.vertical, 8)
    }

    private var library: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Filter maps", selection: $model.filter) {
                    ForEach(["All maps", "Single player", "Multiplayer", "Verified", "Not verified", "Known issue"], id: \.self) { Text($0) }
                }.labelsHidden()
                Spacer()
                Picker("Sort maps", selection: $model.sort) {
                    ForEach(["Name A–Z", "Name Z–A", "Created newest", "Updated newest", "Author"], id: \.self) { Text($0) }
                }.labelsHidden()
            }.padding(12)
            Divider()
            if !model.loaded {
                empty("Opening your library", symbol: "map", text: "Reading local information. This is not an empty library.")
            } else if !model.available {
                empty("Steam Mac game unavailable", symbol: "externaldrive.badge.questionmark", text: "Choose the steamapps folder or inspect Installation & Recovery.") {
                    Button("Choose Steam Library…", action: chooseLibrary)
                    Button("Installation & Recovery…") { advanced("setup") }
                }
            } else if model.maps.isEmpty {
                empty("No maps yet", symbol: "map", text: model.enrolled ? "Import a map archive or browse Collection." : "Complete setup before importing maps.") {
                    Button(model.enrolled ? "Import Map…" : "Installation & Recovery…") { model.enrolled ? importMap() : advanced("setup") }
                    Button("Browse Collection") { model.destination = "Collection" }
                }
            } else if model.visible.isEmpty {
                empty("No matching maps", symbol: "magnifyingglass", text: "Your library remains intact.") {
                    Button("Clear Search & Filters") { model.query = ""; model.filter = "All maps" }
                }
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 155), spacing: 16)], alignment: .leading, spacing: 20) {
                        ForEach(model.visible) { map in
                            Button { model.select(map) } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    artwork(map).frame(height: 108)
                                    Text(map.name).font(.body.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                                    Text(map.status).font(.caption).foregroundStyle(map.warning ? .orange : .secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
                                    .background(model.selection == map.id ? Color.accentColor.opacity(0.15) : .clear, in: RoundedRectangle(cornerRadius: 8))
                            }.buttonStyle(.plain)
                                .accessibilityElement(children: .combine)
                                .accessibilityLabel("\(map.name), \(map.status)")
                                .accessibilityValue(model.selection == map.id ? "Selected" : "Not selected")
                                .disabled(model.working)
                        }
                    }.padding(16).background(ScrollPosition(memory: model.gridScroll))
                }.hardScrollEdge()
                    .onMoveCommand { direction in moveSelection(direction) }
            }
        }
    }

    @ViewBuilder private var destination: some View {
        switch model.destination {
        case "Preferences": preferences
        case "Activity": activity
        case "Collection": collection
        case "Original Game": original
        default:
            if let map = model.selected, model.visible.contains(where: { $0.id == map.id }) {
                detail(map)
            } else {
                empty("Choose a visible map", symbol: "map", text: model.selected == nil ? "Select a map to inspect it." : "The selected map is hidden by the search or filter. Clear them or select another map.")
            }
        }
    }

    private func detail(_ map: MapItem) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(map.name).font(.title2.weight(.semibold)).textSelection(.enabled)
                        Text(map.title.isEmpty ? "Local map edition" : "Personal display name · \(map.packageName)").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if model.section == "Overview" {
                        Button("Play", systemImage: "play.fill") { Task { await model.workflow("play", profile: map.id) } }
                            .primaryPlayStyle().accessibilityLabel("Play selected map").disabled(model.working || !model.enrolled || !model.connected)
                    }
                }
                if map.warning {
                    Label {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(map.status).fontWeight(.semibold)
                            if !map.historyError.isEmpty { Text(map.historyError).font(.caption) }
                            if !map.notice.isEmpty { Text(map.notice).font(.caption) }
                            Text("A later successful play does not clear an unresolved failure.").font(.caption)
                        }
                    } icon: { Image(systemName: "exclamationmark.triangle") }
                    .foregroundStyle(.orange).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                }
                HStack {
                    Button("Edit Details…") { model.edit(map) }
                    Button("Record Outcome…") { model.record(map) }
                }.disabled(model.working)
                Picker("Map section", selection: $model.section) {
                    ForEach(["Overview", "Play History", "Issues & Evidence", "Edit Details", "Record Outcome"], id: \.self) { Text($0) }
                }.labelsHidden().pickerStyle(.menu)
                Divider()
                switch model.section {
                case "Edit Details": editor(map)
                case "Record Outcome": recorder(map)
                case "Play History": history(map)
                case "Issues & Evidence": evidence(map)
                default: overview(map)
                }
            }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                .background(ScrollPosition(memory: model.memory(map.id + ":" + model.section)))
        }.hardScrollEdge().id(map.id + ":" + model.section)
    }

    private func overview(_ map: MapItem) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            artwork(map).frame(height: 155)
            fact(model.translated("Author"), map.metadata.text("author", model.translated("Author not provided")))
            fact("Map type", map.metadata.text("map_type", "Not provided"))
            fact("Created", map.metadata.text("created", "Date not provided"))
            fact("Mac testing", map.verification)
            Divider()
            Text(model.translated("Briefing")).font(.headline)
            Text(map.metadata.text("description").isEmpty ? "No briefing provided." : map.metadata.text("description")).textSelection(.enabled)
            HStack {
                Button("Read Aloud") { advanced("read", profile: map.id) }
                Button("Stop Reading") { advanced("stop-reading", profile: map.id) }
                Button("Translate…") { advanced("translate", profile: map.id) }
            }.disabled(model.working)
            if !map.notes.isEmpty { Text("Personal notes").font(.headline); Text(map.notes).textSelection(.enabled) }
            DisclosureGroup("Package Text & Sources") {
                VStack(alignment: .leading, spacing: 12) {
                    Text(map.metadata.text("raw_text", "No package text provided.")).textSelection(.enabled)
                    ForEach(map.metadata["source_urls"] as? [String] ?? [], id: \.self) { url in
                        if let target = URL(string: url), ["http", "https"].contains(target.scheme?.lowercased() ?? "") { Link(url, destination: target) }
                    }
                }.padding(.top, 8)
            }
        }
    }

    private func editor(_ map: MapItem) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Personal details & images").font(.headline)
            Text("Package facts stay read-only. Personal changes affect presentation only.").foregroundStyle(.secondary)
            DisclosureGroup("Review Package Facts") {
                VStack(alignment: .leading, spacing: 8) {
                    fact("Package name", map.packageName)
                    fact("Author", map.metadata.text("author", "Author not provided"))
                    fact("Version", map.metadata.text("version", "Not provided"))
                }.padding(.top, 8)
            }
            TextField("Personal display name", text: personalBinding(map, \.title))
            Text("Personal notes").font(.headline)
            TextEditor(text: personalBinding(map, \.notes)).frame(minHeight: 100).border(.quaternary)
                .accessibilityLabel("Personal notes")
            HStack(spacing: 16) {
                artwork(map, draft: model.personal[map.id]?.imagePNG).frame(width: 88, height: 72)
                VStack(alignment: .leading, spacing: 6) {
                    Button("Choose Local Image…") { chooseImage(map) }
                    Button("Use Package Image") {
                        var draft = model.personal[map.id] ?? PersonalDraft(title: map.title, notes: map.notes)
                        draft.imagePNG = ""; draft.dirty = true; model.personal[map.id] = draft
                    }
                }
            }
            Text("A bounded copy is saved for this edition. Original package images remain intact.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Cancel") {
                    if model.personal[map.id]?.dirty == true { discardMap = map.id; showingDiscard = true }
                    else { model.section = "Overview" }
                }
                Spacer()
                Button("Keep Draft & Close") { model.section = "Overview" }
                Button("Save Details") { Task { await model.savePersonal(map) } }.buttonStyle(.borderedProminent)
            }.disabled(model.working)
        }
    }

    private func recorder(_ map: MapItem) -> some View {
        let data = model.details[map.id] ?? [:]
        let events = data.rows("history")
        let opens = unique(events.filter { ["process_confirmed", "launch_attempted"].contains($0.text("kind")) }, "attempt_id")
        let draft = model.outcomes[map.id] ?? OutcomeDraft()
        let games = unique(events.filter { ["game_started", "game_resumed"].contains($0.text("kind")) && $0.text("scenario") == draft.scenario }, "game_id")
        let failures = unique(events.filter { ["crash_load", "crash_play", "crash_unknown", "resource_stop"].contains($0.text("kind")) && $0.text("attempt_id") == draft.attempt && ($0.text("scenario").isEmpty || $0.text("scenario") == draft.scenario) }, "incident_id")
        return VStack(alignment: .leading, spacing: 16) {
            Text("Record this session").font(.headline)
            Text("Map and edition are fixed. A process exit leaves gameplay unknown until you explicitly confirm an outcome.").foregroundStyle(.secondary)
            if opens.isEmpty {
                Text("No recorded launch session for this edition/build. A report needs an existing attempt or confirmed process.")
                Button("Refresh History") { Task { await model.loadDetails(map.id, force: true) } }
                Button("Record Legacy Mac Test…") { advanced("legacy-test", profile: map.id) }
            } else {
                Picker("Launch session", selection: outcomeBinding(map.id, \.attempt)) {
                    Text("Choose recorded session").tag("")
                    ForEach(opens.indices, id: \.self) { index in
                        Text("\(opens[index].text("at")) · \(opens[index].text("attempt_id").prefix(12))").tag(opens[index].text("attempt_id"))
                    }
                }
                Picker("Outcome", selection: outcomeBinding(map.id, \.outcome)) {
                    Text("Choose outcome").tag("")
                    ForEach(["Played", "Problem", "Saved", "Resumed", "Completed"], id: \.self) { Text($0) }
                }
                if !draft.outcome.isEmpty {
                    Picker("Scenario", selection: outcomeBinding(map.id, \.scenario)) {
                        Text("Unknown").tag("")
                        ForEach(data["scenarios"] as? [String] ?? [], id: \.self) { Text($0) }
                    }
                    if draft.outcome != "Problem" {
                        Picker("Game", selection: outcomeBinding(map.id, \.game)) {
                            Text("Use this session’s recorded game, if bound").tag("")
                            Text("Start a new game").tag("new")
                            ForEach(games.indices, id: \.self) { index in
                                Text("Recorded game \(games[index].text("game_id").prefix(12))").tag(games[index].text("game_id"))
                            }
                        }
                    }
                    if draft.outcome == "Problem" {
                        Picker("What happened?", selection: outcomeBinding(map.id, \.problem)) {
                            Text("Crash · phase unknown").tag("crash_unknown")
                            Text("Crashed during load").tag("crash_load")
                            Text("Crashed during play").tag("crash_play")
                            Text("Resource stop").tag("resource_stop")
                        }
                        Picker("Incident", selection: outcomeBinding(map.id, \.incident)) {
                            Text("New player-reported failure").tag("")
                            ForEach(failures.indices, id: \.self) { index in
                                Text("\(failures[index].text("at")) · \(failures[index].text("label", failures[index].text("kind")))").tag(failures[index].text("incident_id"))
                            }
                        }
                        Picker("Failed checkpoint", selection: outcomeBinding(map.id, \.checkpoint)) {
                            Text("Unknown").tag("")
                            Text("Map loaded").tag("map_loaded")
                            Text("Playable").tag("playable")
                            Text("Save made").tag("save_made")
                            Text("Exact save reopened").tag("save_reopened")
                            Text("Continued play").tag("continued_play")
                        }
                    } else if ["Saved", "Resumed"].contains(draft.outcome) {
                        Picker("Exact save SHA-256", selection: outcomeBinding(map.id, \.save)) {
                            Text("Choose exact save").tag("")
                            ForEach(Array(Set(events.map { $0.text("save_id") }.filter { !$0.isEmpty } + (draft.save.isEmpty ? [] : [draft.save]))).sorted(), id: \.self) { Text($0).tag($0) }
                        }
                        Button("Choose Exact Save…") { chooseSave(map) }
                        Text("Read-only identity check. Only its digest is recorded; save bytes and path are not stored.").font(.caption).foregroundStyle(.secondary)
                        if draft.outcome == "Resumed" {
                            Toggle("I also continued playing after reopening this exact save", isOn: Binding(get: { model.outcomes[map.id]?.continued ?? false }, set: { value in var draft = model.outcomes[map.id] ?? OutcomeDraft(); draft.continued = value; model.outcomes[map.id] = draft }))
                        }
                    } else if draft.outcome == "Completed" {
                        Picker("Completion reason", selection: outcomeBinding(map.id, \.reason)) {
                            Text("Choose reason").tag("")
                            Text("Met all scenario goals").tag("goals_met")
                            Text("Retired").tag("retired")
                        }
                    }
                    Text(confirmation(draft)).font(.callout)
                }
                HStack {
                    Button("Keep Draft & Close") { model.section = "Overview" }
                    Spacer()
                    Button("Record Confirmation") { Task { await model.confirm(map) } }
                        .buttonStyle(.borderedProminent).disabled(draft.outcome.isEmpty || model.working)
                }
            }
        }.disabled(model.working)
    }

    private func history(_ map: MapItem) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            if !map.historyError.isEmpty { Text("History needs inspection: " + map.historyError).foregroundStyle(.orange) }
            else {
                Text("\(map.summary["opens"] as? Int ?? 0) opens · \(map.summary["started"] as? Int ?? 0) started · \(map.summary["completed"] as? Int ?? 0) completed · \(map.summary["crashes"] as? Int ?? 0) crashes").font(.headline)
                Text("For this exact edition/build. Opens describe observed processes.").foregroundStyle(.secondary)
            }
            let events = model.details[map.id]?.rows("history") ?? []
            if events.isEmpty { Text("No history recorded. Unobserved checkpoints remain unknown.") }
            ForEach(events.indices, id: \.self) { index in
                let event = events[index]
                VStack(alignment: .leading, spacing: 5) {
                    Text(event.text("label", event.text("kind"))).fontWeight(.semibold)
                    Text(event.text("at")).font(.caption).foregroundStyle(.secondary)
                    Text("\(event.text("scenario").isEmpty ? "Scenario unknown" : event.text("scenario")) · \(event.text("source"))").font(.caption)
                    if !event.text("save_id").isEmpty { Text("Save SHA-256: " + event.text("save_id")).font(.caption.monospaced()).textSelection(.enabled) }
                }
                Divider()
            }
            HStack {
                Button("Record Outcome…") { model.record(map) }
                Button("Record Legacy Mac Test…") { advanced("legacy-test", profile: map.id) }
            }
        }
    }

    private func evidence(_ map: MapItem) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(map.verification).font(.headline)
            Text(model.details[map.id]?.text("verification_text", "Reading current evidence…") ?? "Reading current evidence…").textSelection(.enabled)
            DisclosureGroup("Exact Identities & Provenance") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Archive SHA-256: \(map.archive)")
                    Text("Variant: \(map.variant)")
                    Text("Executable SHA-256: \(model.details[map.id]?.object("context").text("game_executable_sha256") ?? "Unknown")")
                }.font(.caption.monospaced()).textSelection(.enabled).padding(.top, 8)
            }
            DisclosureGroup("Static Import Checks") {
                Text(model.details[map.id]?.text("checks_text", "No scan result available.") ?? "No scan result available.").textSelection(.enabled).padding(.top, 8)
                Text("Static findings do not establish gameplay compatibility.").font(.caption)
            }
            DisclosureGroup("Compatibility, Issues & Fixes") {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Existing protected tools retain exact identities, prerequisites and confirmations.")
                    Button("Compatibility & Edition Tools…") { advanced("details", profile: map.id) }
                    Button("Link Issue or Record Fix / Test…") { advanced("fix-test", profile: map.id) }
                    Button("Record Legacy Mac Test…") { advanced("legacy-test", profile: map.id) }
                }.padding(.top, 8)
            }
        }
    }

    private var preferences: some View {
        Form {
            Picker("Category", selection: $preference) {
                ForEach(["General", "Updates", "Library", "Crash Reporting"], id: \.self) { Text($0) }
            }
            Section(preference) {
                if preference == "General" {
                    Picker("Interface language", selection: preferenceText("language", fallback: "en")) {
                        ForEach(model.preferences.rows("languages").indices, id: \.self) { i in
                            let row = model.preferences.rows("languages")[i]
                            Text(row.text("name")).tag(row.text("id"))
                        }
                    }
                    Picker("Briefing voice", selection: preferenceText("voice")) {
                        Text("System default").tag("")
                        ForEach(model.preferences.rows("voices").indices, id: \.self) { i in
                            let row = model.preferences.rows("voices")[i]
                            Text("\(row.text("name")) · \(row.text("language"))").tag(row.text("id"))
                        }
                    }
                    HStack {
                        Button("Refresh Voices") { Task { await model.request("prefs", ["refresh_voices": true]) { model.preferences = $0 } } }
                        Button("Manage Voices…") { advanced("voice-settings") }
                        Button("Translation Languages…") { advanced("translation-settings") }
                    }
                    Text("Language and voice save immediately. Appearance, Liquid Glass tint and accessibility settings follow macOS.")
                    Text("Existing translations cover shared library labels and advanced tools. New workflow copy currently uses English.").font(.caption).foregroundStyle(.secondary)
                    if !model.preferences.text("voice_notice").isEmpty { Text(model.preferences.text("voice_notice")).foregroundStyle(.orange) }
                } else if preference == "Updates" {
                    Toggle("Automatically check for launcher updates", isOn: preferenceFlag("automatic_updates"))
                    Button("Check for Updates…") { advanced("updates") }
                    Text("Updates verify the publisher and staged app before installation.")
                } else if preference == "Library" {
                    Button("Choose Steam Library…", action: chooseLibrary)
                    Button("Installation & Recovery…") { advanced("setup") }
                    Text("Setup, activation and restoration retain the existing stopped-game and preservation checks.")
                } else {
                    Toggle("Monitor game crashes", isOn: preferenceFlag("crash_enabled")).disabled(model.preferences["crash_enabled"] is NSNull)
                    Toggle("Stop runaway game memory use", isOn: preferenceFlag("resource_protection")).disabled(model.preferences["resource_protection"] is NSNull)
                    Toggle("Automatically report sanitized crashes to GitHub", isOn: preferenceFlag("crash_automatic")).disabled(model.preferences["crash_automatic"] is NSNull)
                    Text(model.crashStatus).foregroundStyle(.secondary)
                    Text("Crash preferences save immediately. Full diagnostics stay private. Public reports exclude personal paths, save names, raw logs and attachments.")
                    Button("Review Local Reports…") { advanced("crash-reports") }
                }
            }
        }.formStyle(.grouped).disabled(model.working || !model.connected)
    }

    private var activity: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                Text("Activity").font(.title2.weight(.semibold))
                if model.activity.isEmpty { Text("No launcher activity recorded.").foregroundStyle(.secondary) }
                ForEach(model.activity.indices, id: \.self) { index in
                    let event = model.activity[index]
                    Text(event.text("action") + " · " + event.text("state")).fontWeight(.semibold)
                    Text(event.text("detail")).textSelection(.enabled)
                    Text(event.text("time")).font(.caption).foregroundStyle(.secondary)
                    Divider()
                }
                Button("Refresh Activity") { Task { await model.loadActivity() } }
            }.padding(24)
        }.hardScrollEdge()
    }

    private var collection: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Collection").font(.title2.weight(.semibold))
            TextField("Search collection", text: $model.collectionQuery).textFieldStyle(.roundedBorder)
            Text(model.collectionNotice).font(.caption).foregroundStyle(.secondary)
            let records = model.collection.filter { model.collectionQuery.isEmpty || $0.text("name").localizedCaseInsensitiveContains(model.collectionQuery) }
            List(selection: $model.selectedRemote) {
                ForEach(records.indices, id: \.self) { index in
                    let item = records[index]
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.text("name")).fixedSize(horizontal: false, vertical: true)
                        Text(ByteCountFormatter.string(fromByteCount: Int64(item["size"] as? Int ?? 0), countStyle: .file)).font(.caption).foregroundStyle(.secondary)
                    }.tag(item.text("name"))
                }
            }
            HStack {
                Button("Refresh from Internet Archive") { Task { await model.loadCollection(refresh: true) } }
                Spacer()
                Button("Download & Import") { if let name = model.selectedRemote { Task { await model.workflow("download", extras: ["name": name]) } } }
                    .buttonStyle(.borderedProminent).disabled(model.selectedRemote == nil || !model.enrolled)
            }
            Button("Bulk Download & Collection Tools…") { advanced("collection") }
        }.padding(24).disabled(model.working || !model.connected)
    }

    private var original: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Original Game").font(.title2.weight(.semibold))
            Text("Play the original Steam Mac edition. Existing profile and preservation guards run before launch.")
            Button("Play Original Game", systemImage: "play.fill") { Task { await model.workflow("play", profile: "original-game") } }.primaryPlayStyle()
                .disabled(!model.enrolled || model.working || !model.connected)
            Button("Installation & Recovery…") { advanced("setup") }
            Spacer()
        }.padding(24)
    }

    private func fact(_ label: String, _ value: String) -> some View {
        LabeledContent(label, value: value.isEmpty ? "Not provided" : value).textSelection(.enabled)
    }
    private func artwork(_ map: MapItem, draft: String? = nil) -> some View {
        let picture = draft == "" ? map.packageImage : draft.flatMap { Data(base64Encoded: $0) }.flatMap { NSImage(data: $0) } ?? map.image
        return ZStack {
            RoundedRectangle(cornerRadius: 5).fill(.quaternary)
            if let picture { Image(nsImage: picture).resizable().scaledToFit().padding(3) }
            else { Image(systemName: "photo").font(.title2).foregroundStyle(.secondary) }
        }.accessibilityLabel(picture == nil ? "No map image" : "Map image")
    }
    private func empty<Actions: View>(_ title: String, symbol: String, text: String, @ViewBuilder actions: () -> Actions) -> some View {
        VStack(spacing: 16) {
            Image(systemName: symbol).font(.largeTitle).foregroundStyle(.secondary)
            Text(title).font(.title2.weight(.semibold))
            Text(text).foregroundStyle(.secondary).multilineTextAlignment(.center)
            actions()
        }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    private func empty(_ title: String, symbol: String, text: String) -> some View { empty(title, symbol: symbol, text: text) { EmptyView() } }
    private func symbol(_ destination: String) -> String {
        ["Map Library": "map", "Collection": "square.grid.2x2", "Original Game": "tram", "Activity": "clock", "Preferences": "gearshape"][destination] ?? "map"
    }
    private func personalBinding(_ map: MapItem, _ key: WritableKeyPath<PersonalDraft, String>) -> Binding<String> {
        Binding(get: { (model.personal[map.id] ?? PersonalDraft(title: map.title, notes: map.notes))[keyPath: key] }, set: { value in
            var draft = model.personal[map.id] ?? PersonalDraft(title: map.title, notes: map.notes)
            draft[keyPath: key] = value; draft.dirty = true; model.personal[map.id] = draft
        })
    }
    private func outcomeBinding(_ id: String, _ key: WritableKeyPath<OutcomeDraft, String>) -> Binding<String> {
        Binding(get: { (model.outcomes[id] ?? OutcomeDraft())[keyPath: key] }, set: { value in
            var draft = model.outcomes[id] ?? OutcomeDraft(); draft[keyPath: key] = value
            if key == \.scenario || key == \.attempt { draft.game = ""; draft.incident = ""; draft.save = "" }
            model.outcomes[id] = draft
        })
    }
    private func preferenceText(_ key: String, fallback: String = "") -> Binding<String> {
        Binding(get: { model.preferences.text(key, fallback) }, set: { value in Task { await model.savePreference(key, value) } })
    }
    private func preferenceFlag(_ key: String) -> Binding<Bool> {
        Binding(get: { model.preferences.flag(key) }, set: { value in Task { await model.savePreference(key, value) } })
    }
    private func confirmation(_ draft: OutcomeDraft) -> String {
        switch draft.outcome {
        case "Played": return "Record only if you confirm track, stations and trains worked. This does not confirm completion, saving or reopening."
        case "Problem": return "Confirm the actual failure. Retained incident attribution keeps its original occurrence and provenance."
        case "Saved": return "Confirm that this exact save was made. Save existence alone does not establish a successful save."
        case "Resumed": return "Confirm that you reopened this exact save. Continued play is a separate explicit checkpoint."
        case "Completed": return "Confirm the chosen completion reason. Earlier failures remain until an applicable fix test passes."
        default: return ""
        }
    }
    private func unique(_ rows: [[String: Any]], _ key: String) -> [[String: Any]] {
        var seen = Set<String>()
        return rows.reversed().filter { !$0.text(key).isEmpty && seen.insert($0.text(key)).inserted }.reversed()
    }
    private func advanced(_ action: String, profile: String? = nil) {
        Task { await model.workflow(action, profile: profile ?? model.selection) }
    }
    private func moveSelection(_ direction: MoveCommandDirection) {
        let rows = model.visible
        guard !rows.isEmpty, !model.working else { return }
        let index = rows.firstIndex { $0.id == model.selection } ?? 0
        let change = direction == .left || direction == .up ? -1 : 1
        model.select(rows[min(max(index + change, 0), rows.count - 1)])
    }
    private func importMap() {
        guard model.enrolled, !model.working else { return }
        if model.fixture { model.status = "Synthetic fixture: import disabled."; return }
        let panel = NSOpenPanel(); panel.title = "Choose a custom map archive"; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { Task { await model.workflow("import", extras: ["path": url.path]) } }
    }
    private func chooseLibrary() {
        guard !model.working else { return }
        if model.fixture { model.status = "Synthetic fixture: library switching disabled."; return }
        let panel = NSOpenPanel(); panel.title = "Choose the steamapps folder, not Steam.app"; panel.canChooseDirectories = true; panel.canChooseFiles = false
        if panel.runModal() == .OK, let url = panel.url { Task { await model.workflow("choose-library", extras: ["path": url.path]) } }
    }
    private func chooseSave(_ map: MapItem) {
        if model.fixture { model.status = "Synthetic fixture: save access disabled."; return }
        let panel = NSOpenPanel(); panel.title = "Choose the exact save (read only)"
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.request("history.save_digest", ["path": url.path]) { value in
                var draft = model.outcomes[map.id] ?? OutcomeDraft(); draft.save = value.text("save_id"); model.outcomes[map.id] = draft
            } }
        }
    }
    private func chooseImage(_ map: MapItem) {
        if model.fixture { model.status = "Synthetic fixture: image file access disabled."; return }
        let panel = NSOpenPanel(); panel.title = "Choose a local map image"; panel.allowedContentTypes = [.image]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let file = try FileHandle(forReadingFrom: url); defer { try? file.close() }
            let data = try file.read(upToCount: 8 * 1024 * 1024 + 1) ?? Data()
            guard data.count <= 8 * 1024 * 1024, let source = CGImageSourceCreateWithData(data as CFData, nil),
                  let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 360, kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else {
                throw NativeError.message("Image could not be read. Choose a supported image of at most 8 MiB; the previous image and draft are retained.")
            }
            let representation = NSBitmapImageRep(cgImage: thumbnail)
            guard let png = representation.representation(using: .png, properties: [:]), png.count <= 1024 * 1024 else { throw NativeError.message("The prepared image exceeds 1 MiB. Previous image and draft retained.") }
            var draft = model.personal[map.id] ?? PersonalDraft(title: map.title, notes: map.notes)
            draft.imagePNG = png.base64EncodedString(); draft.dirty = true; model.personal[map.id] = draft
        } catch { model.error = error.localizedDescription }
    }
}

extension View {
    @ViewBuilder func primaryPlayStyle() -> some View {
        if #available(macOS 26.0, *) { buttonStyle(.glassProminent) }
        else { buttonStyle(.borderedProminent) }
    }
    @ViewBuilder func hardScrollEdge() -> some View {
        if #available(macOS 26.0, *) { scrollEdgeEffectStyle(.hard, for: .top) }
        else { self }
    }
}

// Preserve ordinary native scroll positions across destination/width changes.
// NSScrollView still owns input, scrolling and accessibility.
struct ScrollPosition: NSViewRepresentable {
    let memory: ScrollMemory
    func makeCoordinator() -> Coordinator { Coordinator(memory) }
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { [weak view, coordinator = context.coordinator] in
            guard let scroll = view?.enclosingScrollView else { return }
            coordinator.attach(scroll)
        }
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) { }
    final class Coordinator {
        let memory: ScrollMemory
        var observer: NSObjectProtocol?
        init(_ memory: ScrollMemory) { self.memory = memory }
        func attach(_ scroll: NSScrollView) {
            let content = scroll.contentView
            content.postsBoundsChangedNotifications = true
            if let point = memory.point { content.scroll(to: point); scroll.reflectScrolledClipView(content) }
            observer = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification, object: content, queue: .main) { [weak self, weak content] _ in
                if let content { self?.memory.point = content.bounds.origin }
            }
        }
        deinit { if let observer { NotificationCenter.default.removeObserver(observer) } }
    }
}
