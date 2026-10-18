// Private acceptance executable: compile instead of SMRLauncher.swift.
// Only the closed synthetic provider runs. No real service/instance lock is used.
import AppKit
import SwiftUI

private typealias PreviewViewState<Value> = SwiftUI.State<Value>

// Only generated fixture names/types enter this optional local diagnostic log.
enum PreviewDragLog {
    private static func argument(_ name: String) -> String? {
        guard let index = CommandLine.arguments.firstIndex(of: name), CommandLine.arguments.indices.contains(index + 1) else { return nil }
        return CommandLine.arguments[index + 1]
    }
    static func write(_ event: String, _ fields: [String: Any] = [:]) {
        guard let path = argument("--drag-log"),
              let provider = argument("--provider") else { return }
        let root = URL(fileURLWithPath: provider).deletingLastPathComponent().deletingLastPathComponent()
        let log = URL(fileURLWithPath: path).standardizedFileURL
        guard log.path.hasPrefix(root.appendingPathComponent(".local").standardizedFileURL.path + "/") else { return }
        let row = fields.merging(["event": event, "syntheticOnly": true]) { _, new in new }
        guard let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) else { return }
        if !FileManager.default.fileExists(atPath: log.path) {
            guard FileManager.default.createFile(atPath: log.path, contents: Data()) else { return }
        }
        guard let handle = try? FileHandle(forWritingTo: log) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data + Data([10]))
    }
}

private struct SyntheticImageDragSource: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> SyntheticImageDragView { SyntheticImageDragView(url: url) }
    func updateNSView(_ view: SyntheticImageDragView, context: Context) { view.url = url; view.needsDisplay = true }
}

private final class SyntheticImageDragView: NSView, NSDraggingSource {
    var url: URL
    private var started = false
    init(url: URL) {
        self.url = url
        super.init(frame: .zero)
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel("Drag generated " + url.lastPathComponent)
    }
    required init?(coder: NSCoder) { fatalError("Programmatic synthetic fixture") }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlBackgroundColor.setFill(); bounds.fill()
        (url.lastPathComponent as NSString).draw(in: bounds.insetBy(dx: 8, dy: 10), withAttributes: [
            .font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.labelColor
        ])
    }
    override func mouseDown(with event: NSEvent) {
        started = false
        PreviewDragLog.write("sourceMouseDown", ["name": url.lastPathComponent])
    }
    override func mouseDragged(with event: NSEvent) {
        guard !started else { return }
        started = true
        let item = NSDraggingItem(pasteboardWriter: url as NSURL)
        let image = NSImage(size: NSSize(width: 160, height: 40))
        image.lockFocus()
        NSColor.controlBackgroundColor.setFill(); NSRect(origin: .zero, size: image.size).fill()
        (url.lastPathComponent as NSString).draw(at: NSPoint(x: 8, y: 12), withAttributes: [.font: NSFont.systemFont(ofSize: 13)])
        image.unlockFocus()
        item.setDraggingFrame(NSRect(origin: convert(event.locationInWindow, from: nil), size: image.size), contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }
    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation { .copy }
    func draggingSession(_ session: NSDraggingSession, willBeginAt screenPoint: NSPoint) {
        PreviewDragLog.write("sourceSessionBegan", ["name": url.lastPathComponent,
            "itemTypes": session.draggingPasteboard.pasteboardItems?.map { $0.types.map(\.rawValue) } ?? []])
    }
    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        PreviewDragLog.write("sourceSessionEnded", ["name": url.lastPathComponent, "operation": operation.rawValue])
    }
}

@main struct NativeWorkflowPreview: App {
    @StateObject private var model: LauncherModel
    @PreviewViewState private var assets: URL?
    private let backend: Backend
    init() {
        let backend = Backend()
        self.backend = backend
        _model = StateObject(wrappedValue: LauncherModel(fixture: false, backend: backend))
    }
    var body: some Scene {
        WindowGroup("ISOLATED SYNTHETIC WORKFLOW — no game or launcher service") {
            HStack(spacing: 0) {
                if CommandLine.arguments.contains("--drag-source") {
                    VStack(alignment: .leading, spacing: 16) {
                        Text("Synthetic Image Sources").font(.headline)
                        Text("Drag one generated file onto the selected map artwork, then Save Details. No Finder or personal files needed.").font(.caption)
                        if let assets {
                            ForEach(["synthetic.png", "synthetic-2.png", "synthetic-3.png", "unsupported.png", "oversized.png"], id: \.self) { name in
                                if CommandLine.arguments.contains("--swiftui-drag-source") {
                                    Text(name).padding(8).background(.quaternary).onDrag {
                                        let provider = NSItemProvider(item: assets.appendingPathComponent(name) as NSURL, typeIdentifier: "public.file-url")
                                        PreviewDragLog.write("swiftUISourceProvider", ["name": name, "registeredTypes": provider.registeredTypeIdentifiers])
                                        return provider
                                    }
                                } else {
                                    SyntheticImageDragSource(url: assets.appendingPathComponent(name)).frame(height: 40)
                                }
                            }
                        }
                        Spacer()
                    }.padding(16).frame(width: 210)
                    Divider()
                }
                LauncherView(model: model).frame(minWidth: 900, minHeight: 590)
            }
                .task {
                    guard !model.loaded else { return }
                    do {
                        guard let python = LauncherModel.argument("--python"), let provider = LauncherModel.argument("--provider") else { throw NativeError.message("Supply the exact synthetic Python/provider paths") }
                        try backend.start(executable: URL(fileURLWithPath: python), arguments: [provider, "--case", LauncherModel.argument("--case") ?? "common"])
                        model.connected = true
                        await model.refresh()
                        await model.loadPreferences()
                        let info = try await backend.call("fixture.info")
                        assets = URL(fileURLWithPath: info.text("assets"), isDirectory: true)
                        model.tools["fixture_assets"] = info.text("assets")
                        model.status = "SYNTHETIC ONLY · generated files: " + info.text("assets")
                        if let map = model.selected {
                            switch LauncherModel.argument("--view") {
                            case "Overview": model.section = "Overview"
                            case "Issues & Evidence": model.section = "Issues & Evidence"
                            case "Edit Details": model.edit(map)
                            default: model.record(map)
                            }
                        }
                    } catch { model.error = error.localizedDescription }
                }
        }.defaultSize(width: 1180, height: 780)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(replacing: .appTermination) { Button("Quit Synthetic Preview") { NSApplication.shared.terminate(nil) }.keyboardShortcut("q") }
        }
    }
}
