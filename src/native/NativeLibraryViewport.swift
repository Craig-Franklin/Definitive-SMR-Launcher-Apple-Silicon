import CoreGraphics
import Foundation

// Pixel offsets remain useful within one layout. A different column count must
// retain the visible map instead: the same offset identifies a different row.
final class NativeLibraryViewport {
    final class Lifetime {
        private(set) var mounted = false
        private(set) var generation = UUID()
        func mount() { mounted = true }
        func unmount() { mounted = false; generation = UUID() }
    }
    struct Callback {
        fileprivate let id: String
        fileprivate let columns: Int
        fileprivate let identities: [String]
        fileprivate let token: UUID
        fileprivate let generation: UUID
    }
    private(set) var columns: Int?
    private(set) var anchor: String?
    private(set) var pending: String?
    private var identities: [String] = []
    private var identitySet: Set<String> = []
    private var pendingToken: UUID?

    func update(frames: [String: CGRect], columns: Int, height: CGFloat, identities: [String]) -> String? {
        if self.identities != identities {
            self.identities = identities
            identitySet = Set(identities)
            self.columns = columns
            anchor = nil; pending = nil; pendingToken = nil
        }
        guard !frames.isEmpty else { return nil }
        if self.columns != columns {
            self.columns = columns
            if let anchor, identitySet.contains(anchor) {
                pending = anchor
                pendingToken = UUID()
                return anchor
            }
        }
        guard pending == nil else { return nil }
        let visible = frames.filter { identitySet.contains($0.key) && $0.value.maxY > 0 && $0.value.minY < height }
        let fullRows = visible.filter { $0.value.minY >= -0.5 }
        let first = (fullRows.isEmpty ? visible : fullRows).min {
            if abs($0.value.minY - $1.value.minY) > 0.5 { return $0.value.minY < $1.value.minY }
            return $0.value.minX < $1.value.minX
        }
        // A retained map can become the second cell in the top row. Keep that
        // identity until scrolling leaves its row, avoiding round-trip drift.
        if let anchor, let old = visible[anchor], let first, abs(old.minY - first.value.minY) <= 0.5 { return nil }
        anchor = first?.key
        return nil
    }

    func isPending(_ id: String, columns: Int) -> Bool { pending == id && self.columns == columns }
    func restored(_ id: String, columns: Int) {
        if isPending(id, columns: columns) { pending = nil; pendingToken = nil }
    }
    func callback(_ id: String, columns: Int, lifetime: Lifetime) -> Callback? {
        guard isPending(id, columns: columns), let pendingToken else { return nil }
        return Callback(id: id, columns: columns, identities: identities, token: pendingToken, generation: lifetime.generation)
    }
    private func accepts(_ callback: Callback, lifetime: Lifetime, identities: [String], destination: String) -> Bool {
        lifetime.mounted && lifetime.generation == callback.generation && destination == "Map Library" &&
            identities == callback.identities && self.identities == callback.identities &&
            pendingToken == callback.token && isPending(callback.id, columns: callback.columns)
    }
    @discardableResult func perform(_ callback: Callback, lifetime: Lifetime, identities: [String], destination: String, action: () -> Void) -> Bool {
        guard accepts(callback, lifetime: lifetime, identities: identities, destination: destination) else { return false }
        action()
        return true
    }
    @discardableResult func complete(_ callback: Callback, lifetime: Lifetime, identities: [String], destination: String) -> Bool {
        guard accepts(callback, lifetime: lifetime, identities: identities, destination: destination) else { return false }
        restored(callback.id, columns: callback.columns)
        return true
    }
    func discard(_ callback: Callback) {
        if pendingToken == callback.token { cancelPending() }
    }
    func cancelPending() {
        if pending != nil { columns = nil }
        pending = nil; pendingToken = nil
    }
}
