import Foundation

@main struct KeyboardChecks {
    static func main() {
        precondition(NativeLibraryKeyboard.columns(in: 350) == 1)
        precondition(NativeLibraryKeyboard.columns(in: 358) == 2)
        precondition(NativeLibraryKeyboard.columns(in: 700) == 4)
        precondition(NativeLibraryKeyboard.columns(in: 0) == 1)
        let cases: [(Int, Int?, Int, LibraryMove, Int?)] = [
            (0, nil, 3, .right, nil), (8, nil, 3, .right, 0),
            (8, 0, 3, .left, 0), (8, 7, 3, .right, 7),
            (8, 1, 3, .up, 1), (8, 4, 3, .up, 1),
            (8, 1, 3, .down, 4), (8, 5, 3, .down, 7),
            (8, 6, 3, .down, 6), (8, 7, 3, .down, 7),
            (8, 2, 3, .right, 3), (8, 3, 3, .left, 2),
            (8, 2, 1, .down, 3), (8, 2, 0, .up, 1),
            (8, 99, 3, .left, 0), (1, 0, 4, .down, 0),
        ]
        for (count, selected, columns, direction, expected) in cases {
            precondition(NativeLibraryKeyboard.nextIndex(count: count, selected: selected, columns: columns, move: direction) == expected)
        }
        for count in [1, 6, 250, 1000] {
            for columns in [1, 2, 3, 5] {
                for index in 0..<count {
                    for direction: LibraryMove in [.left, .right, .up, .down] {
                        let next = NativeLibraryKeyboard.nextIndex(count: count, selected: index, columns: columns, move: direction)!
                        precondition((0..<count).contains(next))
                    }
                }
            }
        }
        print("Grid keyboard arithmetic passed; adaptive columns, vertical stride, incomplete rows, hidden selection and bounded edges. Source/model only; no key delivery or visible focus proof.")
        let viewport = NativeLibraryViewport()
        let identities = (0..<250).map { "map-\($0)" }
        let oldFrames = ["map-30": CGRect(x: 0, y: -90, width: 150, height: 170),
                         "map-35": CGRect(x: 0, y: 100, width: 150, height: 170),
                         "map-36": CGRect(x: 166, y: 100, width: 150, height: 170)]
        precondition(viewport.update(frames: oldFrames, columns: 5, height: 600, identities: identities) == nil)
        precondition(viewport.anchor == "map-35", "Remember the visible row, never the selected first map")
        let resizedFrames = ["map-16": CGRect(x: 0, y: 100, width: 150, height: 170)]
        precondition(viewport.update(frames: resizedFrames, columns: 2, height: 600, identities: identities) == "map-35")
        precondition(viewport.update(frames: resizedFrames, columns: 2, height: 600, identities: identities) == nil)
        precondition(viewport.anchor == "map-35", "Intermediate layout frames must not replace the pending anchor")
        viewport.restored("map-35", columns: 5)
        precondition(viewport.isPending("map-35", columns: 2), "A stale restore completion must not release a newer layout")
        viewport.restored("map-35", columns: 2)
        let restoredFrames = ["map-34": CGRect(x: 0, y: 0, width: 150, height: 170),
                              "map-35": CGRect(x: 166, y: 0, width: 150, height: 170)]
        precondition(viewport.update(frames: restoredFrames, columns: 2, height: 600, identities: identities) == nil)
        precondition(viewport.anchor == "map-35", "A retained map in the second column must not drift on round-trip resize")
        precondition(viewport.update(frames: oldFrames, columns: 5, height: 600, identities: identities) == "map-35")
        let filtered = ["map-16"]
        precondition(viewport.update(frames: resizedFrames, columns: 2, height: 600, identities: filtered) == nil)
        precondition(viewport.anchor == "map-16" && viewport.pending == nil, "Changed query/order invalidates a pending old-list restore")
        precondition(viewport.update(frames: [:], columns: 5, height: 600, identities: filtered) == nil)
        precondition(viewport.columns == 2, "An unmounted grid cannot consume a width transition")
        let partlyVisible = ["map-16": CGRect(x: 0, y: -10, width: 150, height: 900)]
        precondition(viewport.update(frames: partlyVisible, columns: 2, height: 600, identities: filtered) == nil)
        precondition(viewport.anchor == "map-16", "A viewport containing only a partial card still has an anchor")
        precondition(viewport.update(frames: partlyVisible, columns: 5, height: 600, identities: filtered) == "map-16")
        precondition(viewport.isPending("map-16", columns: 5))
        precondition(viewport.update(frames: [:], columns: 5, height: 600, identities: []) == nil)
        precondition(viewport.anchor == nil && viewport.pending == nil, "An empty result invalidates a queued restore before a frame arrives")
        precondition(!viewport.isPending("map-16", columns: 5), "An obsolete empty-list restore cannot run")
        let queued = NativeLibraryViewport()
        let lifetime = NativeLibraryViewport.Lifetime()
        precondition(queued.update(frames: oldFrames, columns: 5, height: 600, identities: identities) == nil)
        precondition(queued.update(frames: resizedFrames, columns: 2, height: 600, identities: identities) == "map-35")
        let callback = queued.callback("map-35", columns: 2, lifetime: lifetime)!
        var actions = 0
        precondition(!queued.perform(callback, lifetime: lifetime, identities: identities, destination: "Map Library") { actions += 1 })
        lifetime.mount()
        precondition(!queued.perform(callback, lifetime: lifetime, identities: [], destination: "Map Library") { actions += 1 })
        precondition(!queued.perform(callback, lifetime: lifetime, identities: identities.reversed(), destination: "Map Library") { actions += 1 })
        precondition(!queued.perform(callback, lifetime: lifetime, identities: identities, destination: "Preferences") { actions += 1 })
        let otherLifetime = NativeLibraryViewport.Lifetime(); otherLifetime.mount()
        precondition(!queued.perform(callback, lifetime: otherLifetime, identities: identities, destination: "Map Library") { actions += 1 })
        precondition(actions == 0, "Changed current list/destination or another view must never scroll before geometry catches up")
        precondition(queued.perform(callback, lifetime: lifetime, identities: identities, destination: "Map Library") { actions += 1 })
        precondition(!queued.complete(callback, lifetime: lifetime, identities: [], destination: "Map Library"))
        precondition(!queued.complete(callback, lifetime: lifetime, identities: identities.reversed(), destination: "Map Library"))
        precondition(!queued.complete(callback, lifetime: lifetime, identities: identities, destination: "Activity"))
        precondition(queued.isPending("map-35", columns: 2))
        lifetime.unmount(); lifetime.mount()
        precondition(!queued.perform(callback, lifetime: lifetime, identities: identities, destination: "Map Library") { actions += 1 })
        precondition(!queued.complete(callback, lifetime: lifetime, identities: identities, destination: "Map Library"))
        let current = queued.callback("map-35", columns: 2, lifetime: lifetime)!
        precondition(queued.complete(current, lifetime: lifetime, identities: identities, destination: "Map Library"))
        precondition(queued.pending == nil && actions == 1)
        precondition(queued.update(frames: oldFrames, columns: 5, height: 600, identities: identities) == "map-35")
        let earlier = queued.callback("map-35", columns: 5, lifetime: lifetime)!
        precondition(queued.update(frames: resizedFrames, columns: 2, height: 600, identities: identities) == "map-35")
        let later = queued.callback("map-35", columns: 2, lifetime: lifetime)!
        queued.discard(earlier)
        precondition(queued.isPending("map-35", columns: 2), "A stale callback cannot discard newer pending work")
        queued.cancelPending() // Destination changed away and back before delivery.
        precondition(!queued.perform(later, lifetime: lifetime, identities: identities, destination: "Map Library") { actions += 1 })
        precondition(actions == 1)
        print("Grid viewport state passed; logical row retained across 5–2–5 columns, stale completion/frames ignored, changed list invalidated. Source/model only; no resize or viewport rendering proof.")
        print("Queued viewport callbacks passed; actual current identity/order, destination, mounted lifetime/generation and pending token guard both action and completion without a new geometry event.")
    }
}
