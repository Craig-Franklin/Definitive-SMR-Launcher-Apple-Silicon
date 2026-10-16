import AppKit
import Darwin
import Foundation

extension ModelChecks {
    @MainActor static func checkPerformance(_ model: LauncherModel, _ backend: Backend) async throws {
        let map = model.maps[0], other = model.maps[1]
        model.query = "Synthetic"; model.filter = "All maps"; model.sort = "Name A–Z"
        _ = model.visible
        let builds = model.visibleBuilds
        model.gridScroll.point = NSPoint(x: 0, y: 123)
        model.status = "Read-only status changed"
        model.destination = "Activity"; model.destination = "Map Library"
        model.personal[other.id]?.notes = "Retained other draft"
        _ = model.visible; _ = model.visible; _ = model.visible
        precondition(model.visibleBuilds == builds, "Navigation, status and drafts must not repeat query/sort")

        _ = try await backend.call("fixture.configure", ["slow_details": true])
        model.selection = map.id; model.details[map.id] = nil
        let before = try await backend.call("fixture.counts")
        async let first: Void = model.loadDetails(map.id)
        async let second: Void = model.loadDetails(map.id)
        try await Task.sleep(nanoseconds: 70_000_000)
        precondition(model.reading == 1 && !model.busy, "A detail read must not own the mutation gate")
        model.select(other)
        precondition(model.selection == other.id, "Cached selection must remain usable during delayed reads")
        _ = await (first, second)
        let after = try await backend.call("fixture.counts")
        precondition((after["details"] as? Int ?? 0) == (before["details"] as? Int ?? 0) + 1, "Concurrent identical detail loads must coalesce")
        model.details[map.id] = nil; model.selection = map.id
        let rapidBefore = try await backend.call("fixture.counts")
        let rapid = Task { await model.loadDetails(map.id) }
        await Task.yield()
        model.select(other)
        await rapid.value
        let rapidAfter = try await backend.call("fixture.counts")
        precondition(rapidAfter["details"] as? Int == rapidBefore["details"] as? Int, "A superseded selection must not enqueue another read")
        await model.loadDetails(map.id, force: true)
        let savedDetails = model.details[map.id]?.object("context").text("variant_id")
        _ = try await backend.call("fixture.configure", ["changed_edition": true])
        await model.loadDetails(map.id, force: true)
        precondition(model.details[map.id]?.object("context").text("variant_id") == savedDetails, "Wrong-edition details must retain last good content")
        precondition(model.error.contains("no longer matches"))

        _ = try await backend.call("fixture.configure", ["changed_build": true])
        let savedSummary = model.maps[0].summary.text("label")
        await model.loadDetails(map.id, force: true)
        precondition(model.details[map.id]?.object("context").text("game_executable_sha256") == map.build)
        precondition(model.maps[0].summary.text("label") == savedSummary && model.error.contains("executable build"), "Wrong-build details retain last good presentation")

        _ = try await backend.call("fixture.configure", ["slow_details": true])
        model.details[map.id] = nil
        let stale = Task { await model.loadDetails(map.id, force: true) }
        while model.reading == 0 { await Task.yield() }
        model.maps[0] = try MapItem(["id": map.id, "name": "Replacement", "archive_sha256": "new", "variant_id": "new"])
        await stale.value
        precondition(model.details[map.id] == nil && model.maps[0].summary.isEmpty, "Late reads cannot overwrite a changed identity")
        model.maps[0] = map

        _ = try await backend.call("fixture.configure", [:])
        model.selection = map.id; model.edit(map)
        model.personal[map.id]?.title = "Personal renamed map"
        model.personal[map.id]?.notes = "Saved synthetic note"
        model.personal[map.id]?.dirty = true
        let annotationBefore = try await backend.call("fixture.counts")
        await model.savePersonal(map)
        let annotationAfter = try await backend.call("fixture.counts")
        precondition(model.error.isEmpty && model.personal[map.id] == nil)
        precondition(model.maps[0].name == "Personal renamed map" && model.maps[0].notes == "Saved synthetic note")
        precondition(annotationAfter["snapshot"] as? Int == annotationBefore["snapshot"] as? Int)
        precondition(annotationAfter["details"] as? Int == annotationBefore["details"] as? Int)
        precondition(model.maps[1].id == other.id && model.personal[other.id]?.notes == "Retained other draft")
        precondition(model.selection == map.id && model.query == "Synthetic" && model.sort == "Name A–Z")
        precondition(model.gridScroll.point == NSPoint(x: 0, y: 123))
        model.query = "Personal renamed"
        precondition(model.visible.map(\.id) == [map.id], "Annotation must invalidate affected presentation/query results")

        _ = try await backend.call("fixture.configure", ["reject_annotations": true])
        model.edit(model.maps[0]); model.personal[map.id]?.notes = "Keep failed draft"
        model.personal[map.id]?.dirty = true
        await model.savePersonal(model.maps[0])
        precondition(model.error == "synthetic annotations failure" && model.personal[map.id]?.notes == "Keep failed draft")
        precondition(model.maps[0].notes == "Saved synthetic note")
        _ = try await backend.call("fixture.configure", ["changed_annotation_edition": true])
        await model.savePersonal(model.maps[0])
        precondition(model.error.contains("needs inspection") && model.personal[map.id]?.notes == "Keep failed draft")
        precondition(model.maps[0].notes == "Saved synthetic note", "Wrong-edition save replies cannot replace presentation")
        _ = try await backend.call("fixture.configure", ["slow_annotations": true])
        let save = Task { await model.savePersonal(model.maps[0]) }
        while !model.busy { await Task.yield() }
        model.personal[map.id]?.notes = "Changed during save"
        await save.value
        precondition(model.maps[0].notes == "Keep failed draft" && model.personal[map.id]?.notes == "Changed during save", "Newer draft edits must survive a completed save")

        try await checkImageSaveOrders(model, backend)

        _ = try await backend.call("fixture.configure", ["reject_snapshot": true, "slow_snapshot": true])
        let refreshBefore = try await backend.call("fixture.counts")
        async let refreshOne: Void = model.refresh()
        async let refreshTwo: Void = model.refresh()
        _ = await (refreshOne, refreshTwo)
        let refreshAfter = try await backend.call("fixture.counts")
        precondition((refreshAfter["snapshot"] as? Int ?? 0) == (refreshBefore["snapshot"] as? Int ?? 0) + 1)
        precondition(model.error == "synthetic snapshot failure" && model.maps.count == 1000 && model.maps[0].name == "Personal renamed map")
        precondition(model.personal[map.id]?.notes == "Changed during save")
        precondition(refreshAfter["details"] as? Int == refreshBefore["details"] as? Int, "Failed refresh must not clear its failure with another read")
        _ = try await backend.call("fixture.configure", ["library_changed_during_snapshot": true])
        let beforeChangedRefresh = try await backend.call("fixture.counts")
        await model.refresh()
        let afterChangedRefresh = try await backend.call("fixture.counts")
        precondition((afterChangedRefresh["snapshot"] as? Int ?? 0) == (beforeChangedRefresh["snapshot"] as? Int ?? 0) + 2, "A library change during refresh must schedule fresh follow-up work")
        print("Stable library checks passed; cached queries, one coalesced delayed read, strict reply identities, targeted annotation save, retained scroll/drafts and failed refresh")

        try await checkImages()
        try await checkDeferredArtwork(backend)
    }

    @MainActor static func checkDeferredArtwork(_ backend: Backend) async throws {
        let model = LauncherModel(fixture: false, backend: backend)
        model.connected = true; model.enrolled = true
        model.maps = try (0..<70).map { index in
            try MapItem(["id": "art-\(index)", "name": "Artwork \(index)", "archive_sha256": "a",
                "variant_id": "b", "game_executable_sha256": "c", "artwork_state": "pending"])
        }
        let representation = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1, pixelsHigh: 1,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 4, bitsPerPixel: 32)!
        let encoded = representation.representation(using: .png, properties: [:])!.base64EncodedString()
        _ = try await backend.call("fixture.configure", ["slow_artwork": true, "art": encoded])
        let before = try await backend.call("fixture.counts")
        async let first: Void = model.loadArtwork("art-0")
        async let duplicate: Void = model.loadArtwork("art-0")
        try await Task.sleep(nanoseconds: 50_000_000)
        precondition(!model.working, "An artwork read must not disable Play or own the mutation gate")
        model.select(model.maps[1])
        _ = await (first, duplicate)
        let after = try await backend.call("fixture.counts")
        precondition((after["artwork"] as? Int ?? 0) == (before["artwork"] as? Int ?? 0) + 1)
        precondition(model.maps[0].image != nil && model.selection == "art-1", "Late artwork may fill its own card, never change selection")
        let stale = Task { await model.loadArtwork("art-1") }
        try await Task.sleep(nanoseconds: 50_000_000)
        model.maps[1] = try MapItem(["id": "art-1", "archive_sha256": "new", "variant_id": "new", "artwork_state": "pending"])
        await stale.value
        precondition(model.maps[1].image == nil && model.maps[1].artworkState == "pending")
        _ = try await backend.call("fixture.configure", ["wrong_artwork": true, "art": encoded])
        await model.loadArtwork("art-2")
        precondition(model.maps[2].image == nil && model.maps[2].artworkState == "error")
        _ = try await backend.call("fixture.configure", ["art": "invalid"])
        await model.loadArtwork("art-3")
        precondition(model.maps[3].image == nil && model.maps[3].artworkMessage.contains("decoded"))
        _ = try await backend.call("fixture.configure", ["art": encoded])
        await model.retryArtwork("art-3")
        precondition(model.maps[3].image != nil && model.maps[3].artworkState == "ready")
        model.selection = "art-0"
        for index in 4..<70 { await model.loadArtwork("art-\(index)") }
        precondition(model.maps.filter { $0.image != nil }.count <= 64 && model.maps[0].image != nil)
        precondition(model.maps[3].artworkState == "pending", "Old offscreen images must be evicted, not marked absent")
        await model.loadArtwork("art-3")
        precondition(model.maps[3].image != nil && model.maps.filter { $0.image != nil }.count <= 64)
        _ = try await backend.call("fixture.configure", ["art": encoded, "slow_artwork": true, "artwork_snapshot": true])
        await model.refresh()
        let generation = model.libraryRevision
        let refreshID = model.maps[0].id
        let delayed = Task { await model.loadArtwork(refreshID) }
        try await Task.sleep(nanoseconds: 50_000_000)
        await model.refresh()
        precondition(model.libraryRevision > generation && model.maps[0].image == nil)
        // SwiftUI task identity includes this generation, even for unchanged editions.
        let current = Task { await model.loadArtwork(refreshID) }
        await delayed.value; await current.value
        precondition(model.maps[0].image != nil, "A fresh generation must fetch after rejecting an older in-flight reply")
        model.maps[0].verification = "Checks pending"
        model.maps[1].verification = "Verified"
        model.maps[2].verification = "Not verified"
        for filter in ["Verified", "Not verified"] {
            model.filter = filter
            precondition(model.pendingVerificationFilter && model.visible.contains { $0.id == refreshID }, "Unchecked rows cannot silently become nonmatches")
        }
        print("Deferred artwork passed; coalescing, Play available, identity/decode/retry, eviction/refetch, unchanged-edition refresh generation and pending filters")
    }

    @MainActor static func checkImages() async throws {
        // Only generated files in a private temporary directory; no user files.
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let representation = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 100, pixelsHigh: 100,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 400, bitsPerPixel: 32)!
        let png = representation.representation(using: .png, properties: [:])!
        let encoded = png.base64EncodedString()
        let map = try MapItem(["id": "image", "name": "Image", "archive_sha256": "a", "variant_id": "b", "art": encoded])
        precondition(map.packageImage != nil && map.packageImage === map.image, "Package artwork must decode only once")
        var budgeted = map
        let personalImage = MapItem.decodeImage(encoded)
        budgeted.applyAnnotation(["image_png": encoded], preparedImage: personalImage, packageImage: nil)
        precondition(budgeted.packageImage == nil && budgeted.image === personalImage, "A targeted save replaces both artwork layers; omitted package artwork must be released")
        let model = LauncherModel(fixture: true)
        model.maps = [map]; model.edit(map)
        model.personal[map.id]?.notes = "Retain notes"
        let url = directory.appendingPathComponent("image.png")
        try png.write(to: url)
        await model.prepareImage(url, for: map)
        precondition(model.personal[map.id]?.image != nil && model.personal[map.id]?.imagePNG != nil)
        let retained = model.personal[map.id]?.imagePNG
        try Data("unsupported".utf8).write(to: url)
        await model.prepareImage(url, for: map)
        precondition(model.error.contains("supported image") && model.personal[map.id]?.imagePNG == retained)
        try Data(repeating: 0, count: 8 * 1024 * 1024 + 1).write(to: url)
        await model.prepareImage(url, for: map)
        precondition(model.error.contains("8 MiB") && model.personal[map.id]?.imagePNG == retained)
        precondition(model.personal[map.id]?.notes == "Retain notes" && model.imageReads.isEmpty)
        model.usePackageImage(map)
        precondition(model.personal[map.id]?.imagePNG == "" && model.personal[map.id]?.image == nil)
        print("Image checks passed; shared package decode, background thumbnail preparation, unsupported/oversized failure retains draft and image")
    }

    @MainActor static func checkImageSaveOrders(_ model: LauncherModel, _ backend: Backend) async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let representation = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1, pixelsHigh: 1, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 4, bitsPerPixel: 32)!
        let png = representation.representation(using: .png, properties: [:])!
        let map = model.maps[0]
        for imageFirst in [false, true] {
            model.personal[map.id] = nil; model.edit(map)
            model.personal[map.id]?.notes = "Changed during save"; model.personal[map.id]?.dirty = true
            let url = directory.appendingPathComponent(imageFirst ? "image-first.png" : "save-first.png")
            precondition(mkfifo(url.path, 0o600) == 0)
            _ = try await backend.call("fixture.configure", ["slow_annotations": true])
            let save = Task { await model.savePersonal(map) }
            while !model.busy { await Task.yield() }
            let image = Task { await model.prepareImage(url, for: map) }
            while model.imageReads[map.id] == nil { await Task.yield() }
            if !imageFirst {
                await save.value
                precondition(model.personal[map.id] != nil && model.section == "Edit Details", "Save cannot discard a newer pending image or close its editor")
            }
            try await Task.detached {
                let file = try FileHandle(forWritingTo: url)
                try file.write(contentsOf: png); try file.close()
            }.value
            await image.value
            await save.value
            precondition(model.personal[map.id]?.imagePNG != nil && model.personal[map.id]?.image != nil, "Both image/save completion orders retain the newer image draft")
        }
        print("Image/save race checks passed for both completion orders")
    }
}
