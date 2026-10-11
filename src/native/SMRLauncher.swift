import AppKit
import SwiftUI

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: LauncherModel?
    var acceptedQuit = false
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model else { return .terminateNow }
        if acceptedQuit || model.fixture { return .terminateNow }
        if model.dirty {
            let alert = NSAlert()
            alert.messageText = "Quit with unsaved drafts?"
            alert.informativeText = "Personal and outcome drafts are retained while the launcher stays open. Save personal details or record confirmations before quitting to keep them."
            alert.addButton(withTitle: "Continue Working")
            alert.addButton(withTitle: "Discard Drafts & Quit")
            guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
        }
        if !model.connected { return .terminateNow }
        Task { await model.quit() }
        return .terminateCancel
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { sender.windows.first { $0.identifier?.rawValue == "launcher" || $0.title == "Map Library" }?.makeKeyAndOrderFront(nil) }
        return true
    }
}

@main struct SMRLauncherApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = LauncherModel(fixture: CommandLine.arguments.contains("--fixture"))
    var body: some Scene {
        Window("Definitive SMR Launcher", id: "launcher") {
            LauncherView(model: model).frame(minWidth: 900, minHeight: 590)
                .preferredColorScheme(model.fixture ? (LauncherModel.argument("--fixture-appearance") == "light" ? .light : LauncherModel.argument("--fixture-appearance") == "dark" ? .dark : nil) : nil)
                .background(FixtureWindowSize(width: model.fixture ? LauncherModel.argument("--fixture-width").flatMap(Double.init) : nil))
                .task {
                    delegate.model = model
                    await model.start()
                }
                .onReceive(NotificationCenter.default.publisher(for: .nativeServicesClosed)) { _ in
                    delegate.acceptedQuit = true
                    NSApplication.shared.terminate(nil)
                }
        }.defaultSize(width: LauncherModel.argument("--fixture-width").flatMap(Double.init) ?? 1180, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(replacing: .appSettings) {
                Button("Preferences…") { model.destination = "Preferences" }.keyboardShortcut(",")
            }
            CommandGroup(after: .appInfo) {
                Button("Check for Updates…") { Task { await model.workflow("updates") } }
            }
        }
    }
}

// Only synthetic preview launches override saved window geometry.
private struct FixtureWindowSize: NSViewRepresentable {
    let width: Double?
    func makeNSView(context: Context) -> Probe { Probe(width: width) }
    func updateNSView(_ view: Probe, context: Context) { }
    final class Probe: NSView {
        let width: Double?
        init(width: Double?) { self.width = width; super.init(frame: .zero) }
        required init?(coder: NSCoder) { fatalError("Programmatic view only") }
        override func viewDidMoveToWindow() {
            guard let width, let window else { return }
            DispatchQueue.main.async { window.setContentSize(NSSize(width: max(900, width), height: 760)) }
        }
    }
}
