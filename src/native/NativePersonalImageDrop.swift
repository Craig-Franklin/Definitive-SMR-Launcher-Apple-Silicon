import AppKit
import SwiftUI
import UniformTypeIdentifiers

private typealias ImageDropViewState<Value> = SwiftUI.State<Value>

@MainActor final class NativeImageDropState: ObservableObject {
    @Published private(set) var pending = false
    private var token = UUID()
    func invalidate() { token = UUID(); pending = false }

    nonisolated static func localURL(_ value: Any?) throws -> URL {
        let url: URL?
        if let value = value as? URL { url = value }
        else if let value = value as? Data { url = URL(dataRepresentation: value, relativeTo: nil) }
        else if let value = value as? String { url = URL(string: value) }
        else { url = nil }
        guard let url, url.isFileURL, url.path.hasPrefix("/"),
              url.host == nil || url.host == "" || url.host == "localhost" else {
            throw NativeError.message("Drop one local image file. Remote URLs and non-file items are not accepted; your previous image and draft are retained.")
        }
        return url
    }
    func receive(_ providers: [NSItemProvider], model: LauncherModel, map: MapItem) -> Bool {
        guard providers.count == 1, let provider = providers.first,
              provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) else {
            model.error = "Drop exactly one local image file. Multiple files, remote URLs and other items are not accepted; your draft is retained."
            return false
        }
        return start(model: model, map: map) { done in
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, error in
                if let error { done(.failure(error)) }
                else { done(Result { try Self.localURL(item) }) }
            }
        }
    }
    @discardableResult
    func start(model: LauncherModel, map: MapItem, prepare: ((URL) async -> Void)? = nil,
               load: (@escaping (Result<URL, Error>) -> Void) -> Void) -> Bool {
        guard !model.fixture, !model.working, model.connected, current(model, map) else {
            model.error = "Select the exact map edition before dropping an image. No image or draft changed."
            return false
        }
        let request = UUID(); token = request; pending = true
        model.error = ""
        let revision = model.reserveImageChoice(map)
        let draftIdentity = model.personal[map.id]?.identity
        load { result in
            Task { @MainActor in
                guard self.token == request else { return }
                defer { if self.token == request { self.pending = false } }
                guard self.current(model, map), model.currentImageChoice(map, revision),
                      model.personal[map.id]?.identity == draftIdentity, !model.working else {
                    model.error = "The selected map, edition, draft or image choice changed while the drop loaded. Your current image and drafts are retained."
                    return
                }
                do {
                    let url = try Self.localURL(result.get())
                    // Recheck before creating/opening a draft, not only after decode.
                    model.edit(map)
                    if let prepare { await prepare(url) }
                    else { await model.prepareImage(url, for: map) }
                } catch { model.error = error.localizedDescription }
            }
        }
        return true
    }
    private func current(_ model: LauncherModel, _ map: MapItem) -> Bool {
        guard let selected = model.selected else { return false }
        return selected.id == map.id && selected.archive == map.archive && selected.variant == map.variant
    }
}

struct NativePersonalImageDrop: ViewModifier {
    @ObservedObject var model: LauncherModel
    let map: MapItem
    @StateObject private var state = NativeImageDropState()
    @ImageDropViewState private var targeted = false
    func body(content: Content) -> some View {
        content
            .onDrop(of: [UTType.fileURL], isTargeted: $targeted) { state.receive($0, model: model, map: map) }
            .onDisappear { state.invalidate() }
            .overlay(alignment: .bottom) {
                if targeted { Text("Drop one local image to edit. Save is still required.").font(.caption).padding(8).background(.regularMaterial) }
                else if state.pending { ProgressView("Reading dropped image…").controlSize(.small).padding(8).background(.regularMaterial) }
            }
    }
}
