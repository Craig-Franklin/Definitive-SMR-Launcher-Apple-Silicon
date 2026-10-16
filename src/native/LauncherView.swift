import AppKit
import SwiftUI
import UniformTypeIdentifiers

// Select public wrappers explicitly for the Command Line Tools compiler.
private typealias ViewState<Value> = SwiftUI.State<Value>

struct LauncherView: View {
    @ObservedObject var model: LauncherModel
    @StateObject private var mapTools = NativeMapToolsState()
    @ViewState private var discardMap: String?
    @ViewState private var showingDiscard = false
    @FocusState private var focusedMapID: String?
    @FocusState private var inspectorFocused: Bool
    @ViewState private var searchFocusRequest: UUID?
    @ViewState private var pendingInspectorFocus = false
    @ViewState private var mountedInspectorID: String?
    @ViewState private var gridViewport = NativeLibraryViewport()
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
                            Button("Back to Library", systemImage: "chevron.left", action: returnToLibrary)
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
                        NativeLibrarySearch(query: $model.query, title: model.translated("Search maps"), focusRequest: searchFocusRequest, active: model.destination == "Map Library", focused: {
                            focusedMapID = nil; inspectorFocused = false
                        }, consumed: { id in
                            if searchFocusRequest == id { searchFocusRequest = nil }
                        }).frame(minWidth: 150, idealWidth: 220, maxWidth: 300)
                        Button("Import Map", systemImage: "square.and.arrow.down", action: importMap)
                            .disabled(!model.enrolled || model.working || !model.connected)
                    }
                    Menu {
                        Button("Refresh Library") { Task { await model.refresh() } }
                        Button("Choose Steam Library…", action: openLibraryPreferences)
                        Divider()
                        Button("Map Compatibility & Editions…") { model.section = "Compatibility & Editions"; model.destination = "Map Library"; model.narrowDetail = true }.disabled(model.selected == nil)
                        Button("Collection") { model.destination = "Collection" }
                        Button("Installation & Recovery…") { model.preferenceCategory = "Library"; model.destination = "Preferences" }
                        Button("Crash Reports…") { model.preferenceCategory = "Crash Reporting"; model.destination = "Preferences" }
                        Button("Check for Updates…") { model.preferenceCategory = "Updates"; model.destination = "Preferences" }
                    } label: { Label("More", systemImage: "ellipsis.circle") }
                    .disabled(model.working || !model.connected)
                }
            }
            .modifier(NativePlayReviewModifier(model: model, tools: mapTools))
            .onChange(of: model.destination) { destination in
                gridViewport.cancelPending()
                if destination != "Map Library" { searchFocusRequest = nil; pendingInspectorFocus = false }
                if destination == "Preferences" { Task { await model.loadPreferences() } }
                if destination == "Activity" { Task { await model.loadActivity() } }
                if destination == "Collection" { Task { await model.loadCollection() } }
            }
            .task(id: model.selection) {
                if let id = model.selection {
                    async let artwork: Void = model.loadArtwork(id)
                    await model.loadDetails(id)
                    await artwork
                }
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
                Button("Focus Search", action: focusSearch).keyboardShortcut("f").hidden()
                Button("Import Map", action: importMap).keyboardShortcut("o").hidden()
            }
            .onExitCommand {
                if model.section == "Edit Details", let map = model.selected, model.personal[map.id]?.dirty == true {
                    discardMap = map.id; showingDiscard = true
                } else { model.section = "Overview"; returnToLibrary() }
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
            if model.destination == "Map Library" && model.loaded && !model.maps.isEmpty {
                Text("Displayed summaries may be stale. Refresh checks external changes; actions validate current records.").foregroundStyle(.secondary)
            }
            HStack {
                if model.working || model.reading > 0 || !model.loaded { ProgressView().controlSize(.small) }
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
            if model.pendingVerificationFilter {
                Text("Unchecked maps remain visible. Select a map to check whether it matches this filter.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 12).padding(.bottom, 8)
            }
            Divider()
            if !model.loaded {
                empty("Opening your library", symbol: "map", text: "Reading local information. This is not an empty library.")
            } else if !model.available {
                empty("Steam Mac game unavailable", symbol: "externaldrive.badge.questionmark", text: "Choose the steamapps folder or inspect Installation & Recovery.") {
                    Button("Choose Steam Library…", action: openLibraryPreferences)
                    Button("Installation & Recovery…") { model.preferenceCategory = "Library"; model.destination = "Preferences" }
                }
            } else if model.maps.isEmpty {
                empty("No maps yet", symbol: "map", text: model.enrolled ? "Import a map archive or browse Collection." : "Complete setup before importing maps.") {
                    Button(model.enrolled ? "Import Map…" : "Installation & Recovery…") { model.enrolled ? importMap() : openLibraryPreferences() }
                    Button("Browse Collection") { model.destination = "Collection" }
                }
            } else if model.visible.isEmpty {
                empty("No matching maps", symbol: "magnifyingglass", text: "Your library remains intact.") {
                    Button("Clear Search & Filters") { model.query = ""; model.filter = "All maps" }
                }
            } else {
                GeometryReader { geometry in
                    LibraryViewportScope { lifetime in
                    ScrollViewReader { scroll in
                        ScrollView {
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: NativeLibraryKeyboard.minimumWidth), spacing: NativeLibraryKeyboard.spacing, alignment: .top)], alignment: .leading, spacing: 20) {
                                ForEach(model.visible) { map in
                                    Button { openMap(map) } label: {
                                        VStack(alignment: .leading, spacing: 8) {
                                            artwork(map).frame(height: 108)
                                            Text(map.name).font(.body.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                                            Text(map.status).font(.caption).foregroundStyle(map.warning ? .orange : .secondary)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
                                            .background(model.selection == map.id ? Color.accentColor.opacity(0.15) : .clear, in: RoundedRectangle(cornerRadius: 8))
                                    }.buttonStyle(.plain)
                                        .focusable()
                                        .focused($focusedMapID, equals: map.id)
                                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(focusedMapID == map.id ? Color.accentColor : .clear, lineWidth: 2))
                                        .id(map.id)
                                        .background(GeometryReader { card in
                                            Color.clear.preference(key: LibraryCardFrames.self, value: [map.id: card.frame(in: .named("libraryViewport"))])
                                        })
                                        .accessibilityElement(children: .combine)
                                        .accessibilityLabel("\(map.name), \(map.status)")
                                        .accessibilityValue(model.selection == map.id ? "Selected" : "Not selected")
                                }
                            }.padding(NativeLibraryKeyboard.padding).background(ScrollPosition(memory: model.gridScroll))
                        }.hardScrollEdge().coordinateSpace(name: "libraryViewport")
                            .onPreferenceChange(LibraryCardFrames.self) { frames in
                                let columns = NativeLibraryKeyboard.columns(in: geometry.size.width)
                                if let id = gridViewport.update(frames: frames, columns: columns, height: geometry.size.height, identities: model.visible.map(\.id)),
                                   let callback = gridViewport.callback(id, columns: columns, lifetime: lifetime) {
                                    DispatchQueue.main.async {
                                        guard gridViewport.perform(callback, lifetime: lifetime, identities: model.visible.map(\.id), destination: model.destination, action: {
                                            scroll.scrollTo(id, anchor: .top)
                                        }) else { gridViewport.discard(callback); return }
                                        DispatchQueue.main.async {
                                            if !gridViewport.complete(callback, lifetime: lifetime, identities: model.visible.map(\.id), destination: model.destination) {
                                                gridViewport.discard(callback)
                                            }
                                        }
                                    }
                                }
                            }
                            .onMoveCommand { direction in moveSelection(direction, width: geometry.size.width) }
                            .onChange(of: focusedMapID) { id in
                                if let id {
                                    model.selection = id
                                    scroll.scrollTo(id)
                                }
                            }
                            .background {
                                if focusedMapID != nil && model.destination == "Map Library" {
                                    Button("Open Selected Map Details", action: openFocusedMap).keyboardShortcut(.return, modifiers: []).hidden()
                                    Button("Preview Selected Map Details", action: openFocusedMap).keyboardShortcut(.space, modifiers: []).hidden()
                                }
                            }
                    }
                    }
                }
            }
        }
    }

    @ViewBuilder private var destination: some View {
        switch model.destination {
        case "Preferences": NativePreferences(model: model)
        case "Activity": activity
        case "Collection": NativeCollection(model: model)
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
                        Button("Play", systemImage: "play.fill") { Task { await mapTools.beginPlay(model, map) } }
                            .primaryPlayStyle().accessibilityLabel("Play selected map").disabled(model.working || !model.enrolled || !model.connected)
                    }
                }
                if map.artworkState == "error" {
                    Label(map.artworkMessage, systemImage: "exclamationmark.triangle").font(.caption)
                    Button("Retry Image") { Task { await model.retryArtwork(map.id) } }
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
                    ForEach(["Overview", "Play History", "Issues & Evidence", "Compatibility & Editions", "History Scope", "Legacy Mac Test", "Edit Details", "Record Outcome"], id: \.self) { Text($0) }
                }.labelsHidden().pickerStyle(.menu).focusable().focused($inspectorFocused)
                    .onAppear {
                        mountedInspectorID = map.id
                        focusInspector(map.id)
                    }
                    .onDisappear { if mountedInspectorID == map.id { mountedInspectorID = nil } }
                Divider()
                switch model.section {
                case "Edit Details": editor(map)
                case "Record Outcome": recorder(map)
                case "Play History": history(map)
                case "Issues & Evidence": evidence(map)
                case "Compatibility & Editions", "History Scope", "Legacy Mac Test":
                    NativeMapToolsView(model: model, tools: mapTools, map: map, section: model.section)
                default: overview(map)
                }
            }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                .background(ScrollPosition(memory: model.memory(map.id + ":" + model.section)))
        }.hardScrollEdge().id(map.id + ":" + model.section)
    }

    private func overview(_ map: MapItem) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            artwork(map).frame(height: 155)
                .modifier(NativePersonalImageDrop(model: model, map: map))
            Text("Drop one local image to edit. Save is still required.").font(.caption).foregroundStyle(.secondary)
            fact(model.translated("Author"), map.metadata.text("author", model.translated("Author not provided")))
            fact("Map type", map.metadata.text("map_type", "Not provided"))
            fact("Created", map.metadata.text("created", "Date not provided"))
            fact("Mac testing", map.verification)
            Divider()
            Text(model.translated("Briefing")).font(.headline)
            let translation = model.translation(map)
            let original = map.metadata.text("description")
            if translation.showingResult, let text = translation.text {
                Text("Translation · \(translation.source) to \(translation.target)").font(.caption).foregroundStyle(.secondary)
                Text(text.isEmpty ? "No briefing provided." : text).textSelection(.enabled)
                Button("Show Original") {
                    var value = model.translation(map); value.showingResult = false; model.translations[map.id] = value
                }
            } else {
                Text(original.isEmpty ? "No briefing provided." : original).textSelection(.enabled)
                if translation.text != nil {
                    Button("Show Translation") {
                        var value = model.translation(map); value.showingResult = true; model.translations[map.id] = value
                    }
                }
            }
            HStack {
                Button(translation.showingResult ? "Read Original Aloud" : "Read Aloud") { advanced("read", profile: map.id) }
                Button("Stop Reading") { advanced("stop-reading", profile: map.id) }
            }.disabled(model.working)
            DisclosureGroup("Translate Briefing") {
                VStack(alignment: .leading, spacing: 10) {
                    let languages = model.details[map.id]?.rows("languages") ?? []
                    if languages.isEmpty { Text("Loading available languages…").foregroundStyle(.secondary) }
                    Picker("From", selection: Binding(get: { model.translation(map).source }, set: { model.setTranslationLanguage(map, \.source, $0) })) {
                        ForEach(languages.indices, id: \.self) { index in
                            Text(languages[index].text("name")).tag(languages[index].text("id"))
                        }
                    }
                    Picker("To", selection: Binding(get: { model.translation(map).target }, set: { model.setTranslationLanguage(map, \.target, $0) })) {
                        ForEach(languages.indices, id: \.self) { index in
                            Text(languages[index].text("name")).tag(languages[index].text("id"))
                        }
                    }
                    Button("Translate") { Task { await model.translateBriefing(map) } }
                        .disabled(model.working || !model.connected || languages.isEmpty || original.isEmpty)
                    Text("Uses Apple’s installed translation languages on macOS 26 or later. Original map text stays intact.").font(.caption).foregroundStyle(.secondary)
                    Button("Manage Translation Languages…") { advanced("translation-settings") }
                        .disabled(model.working || !model.connected)
                }.padding(.top, 8)
            }
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
                    .modifier(NativePersonalImageDrop(model: model, map: map))
                VStack(alignment: .leading, spacing: 6) {
                    Button("Choose Local Image…") { chooseImage(map) }
                    Button("Use Package Image") {
                        model.usePackageImage(map)
                    }
                }
            }
            if model.imageReads[map.id] != nil { ProgressView("Preparing selected image…").controlSize(.small) }
            Text("Drop one local image onto the preview, or choose a file. Save Details keeps a bounded copy for this edition; original images remain intact.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Cancel") {
                    if model.personal[map.id]?.dirty == true { discardMap = map.id; showingDiscard = true }
                    else { model.section = "Overview" }
                }
                Spacer()
                Button("Keep Draft & Close") { model.section = "Overview" }
                Button("Save Details") { Task { await model.savePersonal(map) } }.buttonStyle(.borderedProminent)
                    .disabled(model.imageReads[map.id] != nil)
            }.disabled(model.working)
        }
    }

    private func recorder(_ map: MapItem) -> some View {
        NativeOutcomeView(model: model, map: map)
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
                Button("Record Legacy Mac Test…") { model.section = "Legacy Mac Test" }
            }
            Button("History Scope…") { model.section = "History Scope" }
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
            NativeMapToolsView(model: model, tools: mapTools, map: map, section: "Issues & Evidence")
        }
    }

    private var activity: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                Text("Activity").font(.title2.weight(.semibold))
                NativeToolProgress(model: model)
                Button("Crash Reporting Preferences") { model.preferenceCategory = "Crash Reporting"; model.destination = "Preferences" }
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

    private var original: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Original Game").font(.title2.weight(.semibold))
            Text("Play the original Steam Mac edition. Existing profile and preservation guards run before launch.")
            Button("Play Original Game", systemImage: "play.fill") { Task { await mapTools.beginPlay(model, nil) } }.primaryPlayStyle()
                .disabled(!model.enrolled || model.working || !model.connected)
            OriginalActivationControls(model: model, tools: mapTools)
            Button("Installation & Recovery…") { model.preferenceCategory = "Library"; model.destination = "Preferences" }
            Spacer()
        }.padding(24)
    }

    private func fact(_ label: String, _ value: String) -> some View {
        LabeledContent(label, value: value.isEmpty ? "Not provided" : value).textSelection(.enabled)
    }
    private func artwork(_ map: MapItem, draft: String? = nil) -> some View {
        let picture = draft == nil ? map.image : draft == "" ? map.packageImage : model.personal[map.id]?.image ?? map.image
        return ZStack {
            RoundedRectangle(cornerRadius: 5).fill(.quaternary)
            if let picture { Image(nsImage: picture).resizable().scaledToFit().padding(3) }
            else {
                VStack(spacing: 6) {
                    if map.artworkState == "pending" { ProgressView("Loading image…").controlSize(.small) }
                    else {
                        Image(systemName: "photo").font(.title2).foregroundStyle(.secondary)
                        Text(map.artworkState == "error" ? "Image unavailable" : "No source artwork").font(.caption)
                    }
                }.padding(4)
            }
        }.accessibilityLabel(picture == nil ? (map.artworkState == "pending" ? "Loading map image" : map.artworkState == "error" ? "Map image unavailable" : "No source artwork") : "Map image")
            .help(map.artworkMessage)
            .task(id: "\(model.libraryRevision):\(map.id):\(map.archive):\(map.variant):\(map.build)") { await model.loadArtwork(map.id) }
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
    private func focusSearch() {
        focusedMapID = nil; inspectorFocused = false
        model.destination = "Map Library"
        searchFocusRequest = UUID()
    }
    private func returnToLibrary() {
        model.narrowDetail = false
        searchFocusRequest = nil; inspectorFocused = false
        DispatchQueue.main.async { focusedMapID = model.visible.first(where: { $0.id == model.selection })?.id ?? model.visible.first?.id }
    }
    private func openMap(_ map: MapItem) {
        pendingInspectorFocus = true
        focusedMapID = nil; searchFocusRequest = nil
        model.destination = "Map Library"; model.select(map)
        if mountedInspectorID == map.id { focusInspector(map.id) }
    }
    private func focusInspector(_ id: String) {
        DispatchQueue.main.async {
            if pendingInspectorFocus && mountedInspectorID == id && model.selection == id && model.destination == "Map Library" {
                inspectorFocused = true; pendingInspectorFocus = false
            }
        }
    }
    private func openFocusedMap() {
        if let id = focusedMapID, let map = model.visible.first(where: { $0.id == id }) { openMap(map) }
    }
    private func moveSelection(_ direction: MoveCommandDirection, width: CGFloat) {
        guard focusedMapID != nil, model.destination == "Map Library" else { return }
        let rows = model.visible
        let move: LibraryMove
        switch direction {
        case .left: move = .left
        case .right: move = .right
        case .up: move = .up
        case .down: move = .down
        @unknown default: return
        }
        if let index = NativeLibraryKeyboard.nextIndex(count: rows.count, selected: rows.firstIndex { $0.id == (focusedMapID ?? model.selection) }, columns: NativeLibraryKeyboard.columns(in: width), move: move) {
            model.selection = rows[index].id
            focusedMapID = rows[index].id
        }
    }
    private func importMap() {
        guard model.enrolled, !model.working else { return }
        if model.fixture { model.status = "Synthetic fixture: import disabled."; return }
        let panel = NSOpenPanel(); panel.title = "Choose a custom map archive"; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { Task { await model.workflow("import", extras: ["path": url.path]) } }
    }
    private func openLibraryPreferences() { model.preferenceCategory = "Library"; model.destination = "Preferences" }
    private func chooseLibrary() {
        openLibraryPreferences()
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
        Task { await model.prepareImage(url, for: map) }
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

private struct LibraryViewportScope<Content: View>: View {
    @ViewState private var lifetime = NativeLibraryViewport.Lifetime()
    let content: (NativeLibraryViewport.Lifetime) -> Content
    var body: some View {
        content(lifetime).onAppear { lifetime.mount() }.onDisappear { lifetime.unmount() }
    }
}

private struct LibraryCardFrames: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

// Preserve ordinary native scroll positions within one column layout. The grid
// separately restores its visible map when adaptive columns change.
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
