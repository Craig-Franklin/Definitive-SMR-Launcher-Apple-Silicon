import SwiftUI

private typealias ToolViewState<Value> = SwiftUI.State<Value>

private struct CollectionArchive: Identifiable {
    let data: [String: Any]
    var id: String { data.text("installed_variant_id") + ":" + data.text("name") + ":" + data.text("sha1") }
}

struct NativeCollection: View {
    @ObservedObject var model: LauncherModel
    @ToolViewState private var mode = "Catalogue"
    @ToolViewState private var pending: [[String: Any]] = []
    private var records: [[String: Any]] { mode == "Map Updates" ? model.tools.rows("candidates") : model.collection }
    private var visible: [[String: Any]] { records.filter { model.collectionQuery.isEmpty || $0.text("name").localizedCaseInsensitiveContains(model.collectionQuery) } }
    private var selected: [[String: Any]] {
        var names = Set<String>()
        return records.filter { model.remoteSelection.contains($0.text("name")) && names.insert($0.text("name")).inserted }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Collection").font(.title2.weight(.semibold))
            Picker("Collection source", selection: $mode) {
                Text("Catalogue").tag("Catalogue")
                Text("Map Updates").tag("Map Updates")
            }.pickerStyle(.segmented)
            TextField("Search collection", text: $model.collectionQuery).textFieldStyle(.roundedBorder)
            Text(mode == "Map Updates" ? "Updates import as separate editions. Existing maps and saves remain available." : model.collectionNotice).font(.caption).foregroundStyle(.secondary)
            if visible.isEmpty {
                Text(model.working ? "Reading catalogue; existing library remains available." : mode == "Map Updates" ? "Check for newer matching map archives. No candidates does not establish compatibility." : model.collectionQuery.isEmpty ? "Refresh the catalogue to browse available archives." : "No matching archives. Clear the search to see the catalogue.").foregroundStyle(.secondary)
            }
            List(selection: $model.remoteSelection) {
                ForEach(visible.map { CollectionArchive(data: $0) }) { item in
                    let row = item.data
                    VStack(alignment: .leading, spacing: 4) {
                        Text(row.text("name")).fixedSize(horizontal: false, vertical: true)
                        Text(ByteCountFormatter.string(fromByteCount: Int64(row["size"] as? Int ?? 0), countStyle: .file)).font(.caption).foregroundStyle(.secondary)
                        if !row.text("installed_filename").isEmpty {
                            Text("Installed: \(row.text("installed_filename")) · \(row.text("kind") == "new_version" ? "New version" : "Revised archive")").font(.caption)
                        }
                    }.tag(row.text("name"))
                }
            }
            HStack {
                Button("Select Visible") { model.remoteSelection = Set(visible.map { $0.text("name") }) }
                Button("Clear Selection") { model.remoteSelection.removeAll() }
                Spacer()
                Text("\(selected.count) selected").foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 10) {
                Button(mode == "Map Updates" ? "Check for Map Updates" : "Refresh from Internet Archive") {
                    Task { if mode == "Map Updates" { await model.runTool("collection.updates") } else { await model.loadCollection(refresh: true) } }
                }.disabled(model.working || !model.connected)
                Button("Download & Import Selected…") { pending = selected }
                    .buttonStyle(.borderedProminent).disabled(selected.isEmpty || !model.enrolled || model.working || !model.connected)
            }
            NativeToolProgress(model: model)
            let items = model.toolJob.object("items")
            if !items.isEmpty {
                DisclosureGroup("Per-Archive Progress & Outcomes") {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            ForEach(items.keys.sorted(), id: \.self) { name in
                                let row = items.object(name)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(name).fontWeight(.semibold)
                                    Text(row.text("state"))
                                    if row.text("state") == "Downloading" {
                                        ProgressView(value: Double(row["completed"] as? Int ?? 0), total: Double(max(1, row["total"] as? Int ?? 1)))
                                    }
                                    if !row.text("notice").isEmpty { Text(row.text("notice")).foregroundStyle(.orange) }
                                }
                            }
                        }
                    }.frame(maxHeight: 180)
                }
            }
        }.padding(24)
            .task { await model.loadTools() }
            .confirmationDialog("Download and import \(pending.count) archives?", isPresented: Binding(get: { !pending.isEmpty }, set: { if !$0 { pending = [] } }), titleVisibility: .visible) {
                Button("Download & Import") {
                    let selection = pending.map { ["name": $0.text("name"), "sha1": $0.text("sha1"), "size": $0["size"] ?? 0] }
                    pending = []
                    Task { await model.runTool("collection.import", ["consent": true, "records": selection]) }
                }
                Button("Cancel", role: .cancel) { pending = [] }
            } message: {
                Text("Download up to \(ByteCountFormatter.string(fromByteCount: Int64(pending.reduce(0) { $0 + ($1["size"] as? Int ?? 0) }), countStyle: .file)), plus extracted files. Four bounded downloads run at once; imports run one at a time after fresh stopped-game checks. Maps are not activated. Cancellation retains completed imports and verified archives. Gameplay remains unverified.")
            }
    }
}
