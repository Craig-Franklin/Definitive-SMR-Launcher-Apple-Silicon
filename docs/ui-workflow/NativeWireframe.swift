// Isolated design review. Synthetic data only; no backend, file, network or game access.
// Build into ignored local storage using the command in REVIEW.md.
import AppKit
import SwiftUI

// Select the public property wrapper explicitly: Command Line Tools lacks the new State macro plugin.
private typealias ReviewState<Value> = SwiftUI.State<Value>

private struct ReviewMap: Identifiable, Hashable {
    let id: Int
    let name: String
    let author: String
    let status: String
    var warning: Bool { id == 0 }
}

private let reviewMaps = [
    ReviewMap(id: 0, name: "Cedar Valley", author: "Example author", status: "Known issue · fix not tested"),
    ReviewMap(id: 1, name: "Highland Freight", author: "Author not provided", status: "Not verified"),
    ReviewMap(id: 2, name: "Lake District", author: "Example author", status: "Player-confirmed completion"),
    ReviewMap(id: 3, name: "North Coast", author: "Author not provided", status: "Never opened"),
    ReviewMap(id: 4, name: "The Very Long Name of a Fictional Railway Across the Eastern Counties", author: "Example author", status: "History needs inspection"),
    ReviewMap(id: 5, name: "Unnamed Map", author: "Author not provided", status: "Not verified · no image")
]

private struct ReportDraft {
    var outcome = ""
    var scenario = "Unknown"
    var game = "Unknown"
    var completionReason = "Choose reason"
    var note = ""
}

@main
struct NativeWireframeApp: App {
    var body: some Scene {
        WindowGroup("SMR Native Design Review") {
            NativeWireframe()
                .frame(minWidth: 900, minHeight: 590)
        }
        .defaultSize(width: 1180, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) { }
        }
    }
}

private struct NativeWireframe: View {
    @ReviewState private var selection: Int? = 0
    @ReviewState private var destination: String? = "Map Library"
    @ReviewState private var alternative = "B · Artwork grid"
    @ReviewState private var search = ""
    @ReviewState private var filter = "All maps"
    @ReviewState private var section = "Overview"
    @ReviewState private var appearance = "System"
    @ReviewState private var notice = "Synthetic review only · no backend or game actions"
    @ReviewState private var state = "Populated"
    @ReviewState private var reportDrafts: [Int: ReportDraft] = [:]
    @ReviewState private var preference = "General"
    @ReviewState private var titles: [Int: String] = [:]
    @ReviewState private var notes: [Int: String] = [:]
    @ReviewState private var showDiscard = false

    private var selected: ReviewMap? { reviewMaps.first { $0.id == selection } }
    private var visible: [ReviewMap] {
        reviewMaps.filter {
            (search.isEmpty || $0.name.localizedCaseInsensitiveContains(search)) &&
            (filter == "All maps" || $0.status.contains(filter))
        }
    }
    private var proposedScheme: ColorScheme? {
        appearance == "Light" ? .light : appearance == "Dark" ? .dark : nil
    }

    var body: some View {
        VStack(spacing: 0) {
            NavigationSplitView {
                List(selection: $destination) {
                    Label("Map Library", systemImage: "map").tag("Map Library")
                    Label("Collection", systemImage: "square.grid.2x2").tag("Collection")
                    Label("Original Game", systemImage: "tram").tag("Original Game")
                    Label("Activity", systemImage: "clock").tag("Activity")
                    Section {
                        Label("Preferences", systemImage: "gearshape").tag("Preferences")
                    }
                }
                .navigationSplitViewColumnWidth(min: 160, ideal: 180, max: 230)
            } content: {
                VStack(spacing: 0) {
                    HStack {
                        Picker("Show", selection: $filter) {
                            ForEach(["All maps", "Known issue", "Not verified"], id: \.self) { Text($0) }
                        }.labelsHidden()
                        Spacer()
                        Text("Name A–Z").foregroundStyle(.secondary).font(.caption)
                    }.padding(12)
                    Divider()
                    library
                }
                .navigationTitle("Map Library")
                .frame(minWidth: alternative.hasPrefix("A") ? 260 : 360)
                .navigationSplitViewColumnWidth(min: alternative.hasPrefix("A") ? 260 : 360, ideal: alternative.hasPrefix("A") ? 320 : 480, max: 650)
                .searchable(text: $search, prompt: "Search maps")
                .toolbar {
                    ToolbarItem {
                        Button("Import Map", systemImage: "square.and.arrow.down") {
                            notice = "Import concept only · native picker and guarded import remain required"
                        }
                    }
                }
            } detail: {
                if destination == "Preferences" {
                    preferences
                } else if destination != "Map Library" {
                    ContentUnavailableView(destination ?? "Map Library", systemImage: "sidebar.left", description: Text("Separate destination retained. No operation runs in this design preview."))
                } else if let map = selected, visible.contains(map), !["Empty library", "First load", "No results"].contains(state) {
                    mapDetail(map)
                } else {
                    ContentUnavailableView("Choose a visible map", systemImage: "map", description: Text("Selection is retained when filtered out. Map actions are unavailable."))
                }
            }
            .navigationSplitViewStyle(.balanced)
            Divider()
            HStack {
                Text("Synthetic review").font(.caption).fixedSize()
                Picker("Composition", selection: $alternative) {
                    Text("A · Focused list").tag("A · Focused list")
                    Text("B · Artwork grid").tag("B · Artwork grid")
                }.labelsHidden().pickerStyle(.segmented).frame(width: 270)
                Spacer()
                Picker("Appearance", selection: $appearance) {
                    ForEach(["System", "Light", "Dark"], id: \.self) { Text($0) }
                }.labelsHidden().frame(width: 100)
                Picker("Review state", selection: $state) {
                    ForEach(["Populated", "Refreshing", "Refresh error", "Empty library", "First load", "No results"], id: \.self) { Text($0) }
                }.labelsHidden().frame(width: 160)
            }.padding(.horizontal, 16).padding(.vertical, 10)
            Divider()
            HStack {
                if state == "Refreshing" { ProgressView().controlSize(.small) }
                Text(state == "Refreshing" ? "Refreshing… · last good content retained" : state == "Refresh error" ? "Could not refresh · showing last good content" : notice)
                    .font(.caption).foregroundStyle(state == "Refresh error" ? .orange : .secondary)
                Spacer()
                Button(state == "Refresh error" ? "Retry" : "Refresh") {
                    state = state == "Refresh error" ? "Populated" : "Refreshing"
                }.controlSize(.small)
            }.padding(.horizontal, 16).padding(.vertical, 8)
        }
        .preferredColorScheme(proposedScheme)
        .confirmationDialog("Keep this personal draft?", isPresented: $showDiscard, titleVisibility: .visible) {
            Button("Keep Draft & Close") { section = "Overview" }
            Button("Discard Draft", role: .destructive) {
                if let id = selection { titles[id] = nil; notes[id] = nil }
                section = "Overview"
            }
            Button("Continue Editing", role: .cancel) { }
        } message: { Text("This preview keeps drafts in memory only. No package is changed.") }
    }

    @ViewBuilder private var library: some View {
        if state == "First load" {
            VStack(spacing: 14) { ProgressView(); Text("Opening your library"); Text("Reading local information · not an empty library").font(.caption).foregroundStyle(.secondary) }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if state == "Empty library" {
            ContentUnavailableView { Label("No maps yet", systemImage: "map") } description: { Text("Import an archive or browse Collection.") } actions: {
                Button("Import Map…") { notice = "Import is a stub. No files read." }
                Button("Browse Collection") { destination = "Collection" }
            }
        } else if state == "No results" || visible.isEmpty {
            ContentUnavailableView { Label("No matching maps", systemImage: "magnifyingglass") } description: { Text("Your library remains intact.") } actions: {
                Button("Clear Search & Filters") { search = ""; filter = "All maps"; state = "Populated" }
            }
        } else if alternative.hasPrefix("A") {
            List(visible, selection: $selection) { map in
                HStack(spacing: 12) {
                    imagePlaceholder.frame(width: 46, height: 46)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(map.name).font(.system(size: 13, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                        Text(map.status).font(.caption).foregroundStyle(map.warning ? .orange : .secondary)
                    }
                }.padding(.vertical, 6).tag(map.id)
            }.listStyle(.inset).scrollEdgeEffectStyle(.hard, for: .top)
        } else {
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 155), spacing: 16)], alignment: .leading, spacing: 20) {
                    ForEach(visible) { map in
                        Button {
                            selection = map.id
                        } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                imagePlaceholder.frame(height: 100)
                                Text(map.name).font(.system(size: 13, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                                Text(map.status).font(.caption).foregroundStyle(map.warning ? .orange : .secondary)
                            }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
                                .background(selection == map.id ? Color.accentColor.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 8))
                        }.buttonStyle(.plain)
                        .accessibilityLabel("\(map.name), \(map.status)")
                        .accessibilityValue(selection == map.id ? "Selected" : "Not selected")
                    }
                }.padding(16)
            }.scrollEdgeEffectStyle(.hard, for: .top)
        }
    }

    private var imagePlaceholder: some View {
        RoundedRectangle(cornerRadius: 5)
            .fill(.quaternary)
            .overlay { Image(systemName: "photo").foregroundStyle(.secondary).accessibilityLabel("Synthetic image placeholder") }
    }

    private func textBinding(_ dictionary: Binding<[Int: String]>, id: Int) -> Binding<String> {
        Binding(get: { dictionary.wrappedValue[id] ?? "" }, set: { dictionary.wrappedValue[id] = $0 })
    }

    private func mapDetail(_ map: ReviewMap) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(map.name).font(.title2.weight(.semibold)).textSelection(.enabled)
                        Text("Original edition · synthetic fixture").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Play", systemImage: "play.fill") {
                        notice = "Play is a stub · current validation and known-issue confirmation remain required"
                    }.buttonStyle(.glassProminent).accessibilityLabel("Play selected map — preview only")
                }
                if map.warning {
                    Label {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Known issue · fix not tested").fontWeight(.semibold)
                            Text("Previous failure retained. A later successful play does not clear it.").font(.caption)
                        }
                    } icon: { Image(systemName: "exclamationmark.triangle") }
                    .foregroundStyle(.orange).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                }
                HStack {
                    Button("Edit Details…") { section = "Edit Details" }.accessibilityLabel("Edit Details")
                    Button("Record Outcome…") { section = "Record Outcome" }.accessibilityLabel("Record Outcome")
                }
                Picker("Map section", selection: $section) {
                    ForEach(["Overview", "Play History", "Issues & Evidence", "Edit Details", "Record Outcome"], id: \.self) { Text($0) }
                }.labelsHidden().pickerStyle(.menu)
                Divider()
                if section == "Edit Details" { editor(map) }
                else if section == "Record Outcome" { recorder(map) }
                else if section == "Play History" {
                    Text("3 opens · 1 started · 0 completed · 1 crash").font(.headline)
                    Text("Synthetic timeline for this edition/build. No gameplay result is claimed.").foregroundStyle(.secondary)
                    Label("Process exit observed · outcome unknown", systemImage: "clock")
                    Label("Gameplay explicitly confirmed by player", systemImage: "person")
                    Button("Record Legacy Mac Test…") { notice = "Legacy reports remain separate from exact scenario evidence." }
                } else if section == "Issues & Evidence" {
                    DisclosureGroup("Exact identities & provenance") { Text("Full digests belong here. No real identities are read in this preview.") }
                    DisclosureGroup("Static import checks") { Text("A static result does not certify gameplay compatibility.") }
                    DisclosureGroup("Compatibility options") { Text("Existing reviewed recipe and experimental edition controls remain guarded.") }
                    Button("Record Fix or Test…") { notice = "Fix tests retain exact failure/checkpoint and qualifying session prerequisites." }
                } else {
                    imagePlaceholder.frame(height: 140)
                    LabeledContent("Author", value: map.author)
                    LabeledContent("Map type", value: "Single player · illustrative")
                    LabeledContent("Mac testing", value: "Not verified · exact edition/build")
                    Divider()
                    Text("Briefing").font(.headline)
                    Text("A fictional railway linking inland towns with the coast. Synthetic content only.")
                    HStack {
                        Button("Read Aloud") { notice = "Read-aloud concept only." }
                        Button("Translate…") { notice = "Translation concept only. Original text remains available." }
                    }
                    DisclosureGroup("Package Text & Sources") { Text("Original package facts remain distinct from personal edits.") }
                }
            }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollEdgeEffectStyle(.hard, for: .top)
        .navigationSplitViewColumnWidth(min: 360, ideal: 450, max: 700)
        .navigationTitle(map.name)
    }

    private func editor(_ map: ReviewMap) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Personal details & images").font(.headline)
            Text("Package author: \(map.author). Personal changes affect presentation only.").foregroundStyle(.secondary)
            TextField("Personal display name", text: textBinding($titles, id: map.id))
            VStack(alignment: .leading) {
                Text("Personal notes")
                TextEditor(text: textBinding($notes, id: map.id)).frame(minHeight: 90).border(.quaternary)
            }
            HStack {
                imagePlaceholder.frame(width: 84, height: 64)
                Button("Choose Local Image…") { notice = "Image could not be read · existing image and draft retained (synthetic error)." }
            }
            Text("Local file picker is a stub. No file is read or overwritten.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Cancel") { showDiscard = true }
                Spacer()
                Button("Keep Draft & Close") { section = "Overview"; notice = "Personal draft retained in memory for this map." }
                Button("Save Details") { section = "Overview"; notice = "Synthetic save preview only · no disk write." }.buttonStyle(.borderedProminent)
            }
        }
    }

    private func reportBinding(_ id: Int, _ key: WritableKeyPath<ReportDraft, String>) -> Binding<String> {
        Binding(get: { (reportDrafts[id] ?? ReportDraft())[keyPath: key] }, set: { value in
            var draft = reportDrafts[id] ?? ReportDraft()
            draft[keyPath: key] = value
            reportDrafts[id] = draft
        })
    }

    private func recorder(_ map: ReviewMap) -> some View {
        let draft = reportDrafts[map.id] ?? ReportDraft()
        return VStack(alignment: .leading, spacing: 16) {
            Text("Record this session").font(.headline)
            Text("Known map/edition/build/attempt are prefilled. Process exit observed; outcome unknown.").foregroundStyle(.secondary)
            Picker("Outcome", selection: reportBinding(map.id, \.outcome)) {
                Text("Choose outcome").tag("")
                ForEach(["Played", "Problem", "Saved", "Resumed", "Completed"], id: \.self) { Text($0) }
            }
            if !draft.outcome.isEmpty {
                Picker("Scenario", selection: reportBinding(map.id, \.scenario)) {
                    Text("Unknown"); Text("Coastal scenario")
                }
                if draft.outcome != "Problem" {
                    Picker("Game", selection: reportBinding(map.id, \.game)) {
                        Text("Unknown"); Text("Continue recorded game 1"); Text("Start a new game")
                    }
                }
                if draft.outcome == "Problem" {
                    Text("Choose exact phase/incident, or retain unknown where permitted. No automatic incident binding.")
                } else if draft.outcome == "Saved" || draft.outcome == "Resumed" {
                    Button("Choose Exact Save…") { notice = "Read-only exact-save picker is a stub. No save is read." }
                    Text("Digest required; no save bytes/path stored. Continued play after reopen is separate.").font(.caption).foregroundStyle(.secondary)
                } else if draft.outcome == "Completed" {
                    Picker("Completion reason", selection: reportBinding(map.id, \.completionReason)) {
                        Text("Choose reason"); Text("Met all scenario goals"); Text("Retired")
                    }
                } else {
                    Text("Confirm track, stations and trains worked. This does not confirm completion, save or reload.")
                }
                DisclosureGroup("Optional note") {
                    TextEditor(text: reportBinding(map.id, \.note)).frame(minHeight: 70)
                }
            }
            HStack {
                Button("Keep Draft & Close") { section = "Overview"; notice = "Outcome draft deferred · no event recorded." }
                Spacer()
                Button("Record Confirmation") {
                    notice = "Confirmation is a stub · required bindings and exact evidence validation remain mandatory. No event written."
                }.buttonStyle(.borderedProminent).disabled(draft.outcome.isEmpty)
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
                    LabeledContent("Interface language", value: "English · illustrative")
                    LabeledContent("Briefing voice", value: "System default")
                    Text("Appearance, Liquid Glass tint, Reduce Transparency and Increase Contrast follow macOS settings in the intended native frontend.")
                } else if preference == "Crash Reporting" {
                    Text("Preserve current privacy preferences and capability gates. No collector is armed by this preview.")
                    Text("Public reports exclude personal paths, save names, raw logs and attachments.")
                } else {
                    Text("Existing \(preference.lowercased()) controls belong here. This wireframe changes no settings.")
                }
            }
        }.formStyle(.grouped).navigationTitle("Preferences")
    }
}
