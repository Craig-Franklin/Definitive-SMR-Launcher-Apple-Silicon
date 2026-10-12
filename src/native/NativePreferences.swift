import AppKit
import SwiftUI

private typealias ToolViewState<Value> = SwiftUI.State<Value>

struct NativeToolProgress: View {
    @ObservedObject var model: LauncherModel
    var body: some View {
        if !model.toolJob.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    if model.toolRunning { ProgressView().controlSize(.small) }
                    Text(model.toolJob.text("message", "Working…")).textSelection(.enabled)
                    Spacer()
                    if model.toolRunning && model.toolJob.flag("cancellable") {
                        Button(model.toolJob.flag("cancel_requested") ? "Cancellation Requested" : "Cancel Operation") { Task { await model.cancelTool() } }
                            .disabled(model.toolJob.flag("cancel_requested"))
                    }
                }
                if model.toolRunning && model.toolJob.text("action") == "update.stage" {
                    Text("Cancellation waits for the current download or verification step. It never quits the launcher.").font(.caption).foregroundStyle(.secondary)
                }
                if ["failed", "unknown"].contains(model.toolJob.text("state")) {
                    Label("Inspect current state before repeating an unfinished operation.", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                }
            }
        }
    }
}

struct NativePreferences: View {
    @ObservedObject var model: LauncherModel
    @ToolViewState private var confirmation = ""
    @ToolViewState private var consent: NativeToolConsent?
    private var disabled: Bool { model.working || !model.connected }
    var body: some View {
        Form {
            Picker("Category", selection: $model.preferenceCategory) {
                ForEach(["General", "Updates", "Library", "Crash Reporting"], id: \.self) { Text($0) }
            }
            Section(model.preferenceCategory) {
                switch model.preferenceCategory {
                case "General": general
                case "Updates": updates
                case "Library": library
                default: crash
                }
            }
            Section { NativeToolProgress(model: model) }
        }.formStyle(.grouped)
            .task { await model.loadTools() }
            .confirmationDialog(confirmationTitle, isPresented: Binding(get: { !confirmation.isEmpty }, set: { if !$0 { confirmation = "" } }), titleVisibility: .visible) {
                Button("Confirm") {
                    let captured = consent; confirmation = ""; consent = nil
                    if let captured { Task { await model.submitToolConsent(captured) } }
                }
                Button("Cancel", role: .cancel) { confirmation = "" }
            } message: { Text(confirmationMessage) }
    }
    private var general: some View {
        Group {
            Picker("Interface language", selection: text("language", "en")) {
                ForEach(model.preferences.rows("languages").indices, id: \.self) { i in
                    let row = model.preferences.rows("languages")[i]
                    Text(row.text("name")).tag(row.text("id"))
                }
            }
            Picker("Briefing voice", selection: text("voice")) {
                Text("System default").tag("")
                ForEach(model.preferences.rows("voices").indices, id: \.self) { i in
                    let row = model.preferences.rows("voices")[i]
                    Text(row.text("name") + " · " + row.text("language")).tag(row.text("id"))
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Button("Refresh Voices") { Task { await model.request("prefs", ["refresh_voices": true]) { model.preferences = $0 } } }
                Button("Manage Voices…") { Task { await model.workflow("voice-settings") } }
                Button("Translation Languages…") { Task { await model.workflow("translation-settings") } }
            }.disabled(disabled)
            Text("Language and voice save immediately. Appearance and accessibility follow macOS. New workflow text currently uses English.").font(.caption).foregroundStyle(.secondary)
            if !model.preferences.text("voice_notice").isEmpty { Text(model.preferences.text("voice_notice")).foregroundStyle(.orange) }
        }
    }
    private var updates: some View {
        Group {
            LabeledContent("Installed launcher", value: model.tools.text("version", "Reading…"))
            Toggle("Automatically install verified updates when I quit", isOn: flag("automatic_updates")).disabled(disabled)
            Text("Releases come only from Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon. Downloads must pass archive identity, publisher and staged-app checks.").font(.caption)
            if model.tools.flag("update_pending") {
                Label("Verified update staged. Review install or quit without installing when you quit.", systemImage: "checkmark.shield")
                Button("Review Update & Quit…") { NSApplication.shared.terminate(nil) }.disabled(disabled)
            } else if model.tools.flag("update_available") {
                Text("Version \(model.tools.text("update_version")) is available.")
                Button("Download & Stage Verified Update…") { confirm("update.stage") }.disabled(disabled)
            }
            Button("Check for Updates") { Task { await model.runTool("update.check") } }.disabled(disabled || model.tools.flag("update_pending"))
            Button("Refresh Update Status") { Task { await model.loadTools() } }.disabled(!model.connected)
        }
    }
    private var library: some View {
        Group {
            Text(model.enrolled ? "Original Game and existing saves are preserved." : model.available ? "Steam Mac game found. Complete setup to import maps." : "Steam Mac game unavailable. Retry discovery or choose its Steam library.")
            VStack(alignment: .leading, spacing: 8) {
                Button("Retry Discovery…") { confirm("library.discover") }.disabled(disabled || model.available)
                Button("Choose Steam Library…", action: chooseLibrary).disabled(disabled)
                Button("Complete Setup…") { confirm("library.setup") }.disabled(disabled || !model.available || model.enrolled)
            }
            Text("Setup requires a stopped game and a clean original profile. Existing guarded preservation and recovery checks run before changes. Maps are not activated.").font(.caption)
            Button("Installation Help") { Task { await model.workflow("installation-help") } }.disabled(disabled)
        }
    }
    private var crash: some View {
        Group {
            Toggle("Monitor game crashes", isOn: flag("crash_enabled")).disabled(disabled || !model.tools.flag("monitor_available"))
            Toggle("Stop runaway game memory use", isOn: flag("resource_protection")).disabled(disabled || !model.tools.flag("monitor_available"))
            Toggle("Automatically report sanitized crashes to GitHub", isOn: flag("crash_automatic")).disabled(disabled || !model.tools.flag("monitor_available"))
            Text("Crash preferences save immediately. Full diagnostics stay private. Public reports exclude personal paths, save names, raw logs and attachments.").font(.caption)
            Text(model.tools.text("crash_notice")).foregroundStyle(.secondary)
            let counts = model.tools.object("crash_counts")
            ForEach(counts.keys.sorted(), id: \.self) { key in
                LabeledContent(key.capitalized, value: String(counts[key] as? Int ?? 0))
            }
            Button("Read Local Report Status") { Task { await model.runTool("crash.status") } }.disabled(disabled || !model.tools.flag("monitor_available"))
            HStack {
                Button("Check Pending Reports…") { confirm("crash.reconcile") }
                Button("Open Private Reports Folder") { Task { await model.runTool("crash.folder") } }
            }.disabled(disabled || !model.tools.flag("monitor_available"))
        }
    }
    private var confirmationTitle: String {
        switch confirmation {
        case "update.stage": return "Download and stage this verified release?"
        case "library.setup": return "Preserve the original profile and complete setup?"
        case "crash.reconcile": return "Reconcile pending crash reports?"
        default: return "Find the Steam Mac game?"
        }
    }
    private var confirmationMessage: String {
        switch confirmation {
        case "update.stage": return "Download version \(consent?.params.text("version") ?? "") (\(ByteCountFormatter.string(fromByteCount: Int64(consent?.params["size"] as? Int ?? 0), countStyle: .file))) and verify the publisher. Installation needs a later quit decision. Cancellation keeps the launcher and drafts open."
        case "library.setup": return "The original profile and existing saves are preserved by the existing setup operation. It refuses unsafe or running-game state."
        case "crash.reconcile": return "Check local reports using the existing privacy and publication gates. Saved automatic reporting preferences may permit sanitized publication. Unknown publication outcomes are reconciled, never blindly retried."
        default: return "Inspect the Steam installation and save this library choice: \(consent?.params.text("path", "Saved/default Steam library") ?? ""). Existing binding and stopped-game checks apply."
        }
    }
    private func confirm(_ action: String, path: String = "") {
        consent = model.captureToolConsent(action, path: path); confirmation = action
    }
    private func chooseLibrary() {
        guard !model.fixture else { model.status = "Synthetic fixture: discovery disabled."; return }
        let panel = NSOpenPanel(); panel.title = "Choose the steamapps folder, not Steam.app"
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        if panel.runModal() == .OK, let url = panel.url { confirm("library.choose", path: url.path) }
    }
    private func text(_ key: String, _ fallback: String = "") -> Binding<String> {
        Binding(get: { model.preferences.text(key, fallback) }, set: { value in Task { await model.savePreference(key, value) } })
    }
    private func flag(_ key: String) -> Binding<Bool> {
        Binding(get: { model.preferences.flag(key) }, set: { value in Task { await model.savePreference(key, value) } })
    }
}
