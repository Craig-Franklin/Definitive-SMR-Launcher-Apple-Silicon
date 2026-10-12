import AppKit
import SwiftUI

// NSToolbar can retain its hosting view across destination changes. Bind focus
// to the actual mounted search control instead of that hosting view's appearance.
struct NativeLibrarySearch: NSViewRepresentable {
    @Binding var query: String
    let title: String
    let focusRequest: UUID?
    let active: Bool
    let focused: () -> Void
    let consumed: (UUID) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(query: $query) }
    func makeNSView(context: Context) -> SearchField {
        let field = SearchField(frame: .zero)
        field.delegate = context.coordinator
        field.target = context.coordinator
        field.action = #selector(Coordinator.changed(_:))
        field.sendsSearchStringImmediately = true
        field.sendsWholeSearchString = false
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return field
    }
    func updateNSView(_ field: SearchField, context: Context) {
        context.coordinator.query = $query
        if field.stringValue != query { field.stringValue = query }
        field.placeholderString = title
        field.setAccessibilityLabel(title)
        field.focused = focused
        field.consumed = consumed
        field.active = active
        field.pending = focusRequest
        field.attemptFocus()
    }
    static func dismantleNSView(_ field: SearchField, coordinator: Coordinator) {
        field.active = false
        field.pending = nil
    }

    final class Coordinator: NSObject, NSSearchFieldDelegate {
        var query: Binding<String>
        init(query: Binding<String>) { self.query = query }
        @objc func changed(_ sender: NSSearchField) { query.wrappedValue = sender.stringValue }
        func controlTextDidChange(_ notification: Notification) {
            if let field = notification.object as? NSSearchField { changed(field) }
        }
    }

    final class SearchField: NSSearchField {
        var pending: UUID?
        var active = false
        var focused: (() -> Void)?
        var consumed: ((UUID) -> Void)?
        private var handled: UUID?
        private var keyObserver: NSObjectProtocol?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let keyObserver { NotificationCenter.default.removeObserver(keyObserver) }
            keyObserver = nil
            guard let window else { pending = nil; active = false; return }
            keyObserver = NotificationCenter.default.addObserver(forName: NSWindow.didBecomeKeyNotification, object: window, queue: .main) { [weak self] _ in
                self?.attemptFocus()
            }
            attemptFocus()
        }
        override func becomeFirstResponder() -> Bool {
            let accepted = super.becomeFirstResponder()
            if accepted { DispatchQueue.main.async { [weak self] in self?.focused?() } }
            return accepted
        }
        func attemptFocus() {
            guard active, let id = pending, id != handled, let window, window.isKeyWindow, acceptsFirstResponder,
                  window.makeFirstResponder(self), window.firstResponder === self || currentEditor() != nil else { return }
            handled = id
            DispatchQueue.main.async { [weak self] in
                guard let self, self.active, self.pending == id else { return }
                self.consumed?(id)
            }
        }
        deinit { if let keyObserver { NotificationCenter.default.removeObserver(keyObserver) } }
    }
}
