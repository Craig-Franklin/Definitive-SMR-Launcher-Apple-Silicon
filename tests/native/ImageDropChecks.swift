import AppKit
import Foundation

@main struct ImageDropChecks {
    @MainActor static func main() async throws {
        func require(_ value: Bool, _ message: String, line: UInt = #line) { precondition(value, message + " at line \(line)") }
        let backend = Backend()
        try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2]])
        let model = LauncherModel(fixture: false, backend: backend)
        model.connected = true; await model.refresh()
        let map = model.maps[0], other = model.maps[1], state = NativeImageDropState()
        let info = try await backend.call("fixture.info"), assets = URL(fileURLWithPath: info.text("assets"))
        let valid = assets.appendingPathComponent("synthetic.png"), second = assets.appendingPathComponent("synthetic-2.png")
        func settle() async throws {
            for _ in 0..<100 where state.pending { try await Task.sleep(nanoseconds: 10_000_000) }
            require(!state.pending, "Injected image load completes")
        }
        var callback: ((Result<URL, Error>) -> Void)?
        state.start(model: model, map: map) { callback = $0 }
        model.selection = other.id
        callback?(.success(valid)); try await settle()
        require(model.personal[map.id] == nil && model.personal[other.id] == nil, "Delayed changed selection cannot create/edit a draft")
        model.selection = map.id
        state.start(model: model, map: map) { callback = $0 }
        let changed = try MapItem(["id": map.id, "archive_sha256": map.archive, "variant_id": "different-edition"])
        model.maps[0] = changed
        callback?(.success(valid)); try await settle()
        require(model.personal[map.id] == nil, "Changed edition refuses before draft creation")
        model.maps[0] = map
        state.start(model: model, map: map) { $0(.success(valid)) }; try await settle()
        require(model.section == "Edit Details" && model.personal[map.id]?.dirty == true && model.personal[map.id]?.image != nil, "Valid drop opens exact draft with bounded prepared copy")
        let original = model.personal[map.id]!
        state.start(model: model, map: map) { callback = $0 }
        model.personal[map.id] = nil
        callback?(.success(valid)); try await settle()
        require(model.personal[map.id] == nil, "Discarded draft cannot be resurrected by a late provider")
        model.edit(map)
        state.start(model: model, map: map) { callback = $0 }
        model.personal[map.id] = nil; model.edit(map)
        let recreated = model.personal[map.id]!.identity
        callback?(.success(valid)); try await settle()
        require(model.personal[map.id]?.identity == recreated && model.personal[map.id]?.imagePNG == nil, "Recreated draft cannot inherit old drop consent")
        model.personal[map.id] = nil; model.section = "Overview"
        state.start(model: model, map: map) { callback = $0 }
        state.invalidate()
        callback?(.success(valid)); try await Task.sleep(nanoseconds: 30_000_000)
        require(model.personal[map.id] == nil && model.section == "Overview", "Unmounted source cannot open/edit a draft")
        model.personal[map.id] = original
        var finishPreparation: CheckedContinuation<Void, Never>?
        state.start(model: model, map: map, prepare: { _ in
            await withCheckedContinuation { finishPreparation = $0 }
        }) { $0(.success(valid)) }
        for _ in 0..<100 where finishPreparation == nil { try await Task.sleep(nanoseconds: 1_000_000) }
        require(finishPreparation != nil, "Old preparation reached the injected async boundary")
        state.start(model: model, map: map) { callback = $0 }
        finishPreparation?.resume()
        try await Task.sleep(nanoseconds: 30_000_000)
        require(state.pending, "Old preparation cannot clear a newer pending provider")
        callback?(.success(valid)); try await settle()
        state.start(model: model, map: map) { callback = $0 }
        await model.prepareImage(second, for: map)
        let newer = model.personal[map.id]!
        require(newer.imagePNG != original.imagePNG, "Synthetic replacement differs")
        callback?(.success(valid)); try await settle()
        require(model.personal[map.id]?.imagePNG == newer.imagePNG, "Newer picker image wins delayed drop")
        var olderDrop: ((Result<URL, Error>) -> Void)?
        state.start(model: model, map: map) { olderDrop = $0 }
        state.start(model: model, map: map) { callback = $0 }
        callback?(.success(second)); try await settle()
        olderDrop?(.success(valid)); try await Task.sleep(nanoseconds: 30_000_000)
        require(model.personal[map.id]?.imagePNG == newer.imagePNG, "Newest drop wins reversed provider callback order")
        state.start(model: model, map: map) { callback = $0 }
        model.usePackageImage(map)
        callback?(.success(valid)); try await settle()
        require(model.personal[map.id]?.imagePNG == "", "Package-image choice wins delayed drop")
        await model.prepareImage(valid, for: map)
        for name in ["unsupported.png", "oversized.png"] {
            let before = model.personal[map.id]!
            state.start(model: model, map: map) { $0(.success(assets.appendingPathComponent(name))) }; try await settle()
            require(model.error.contains("supported image") && model.personal[map.id]?.imagePNG == before.imagePNG && model.personal[map.id]?.notes == before.notes, "Invalid image keeps prior draft and image")
        }
        let previous = model.personal[map.id]!
        state.start(model: model, map: map) { $0(.success(URL(string: "https://example.invalid/remote.png")!)) }; try await settle()
        require(model.error.contains("Remote URLs") && model.personal[map.id]?.imagePNG == previous.imagePNG, "Remote URL rejected")
        require(!state.receive([], model: model, map: map), "Missing item rejected")
        let item = NSItemProvider(item: valid as NSURL, typeIdentifier: "public.file-url")
        require(!state.receive([item, item], model: model, map: map) && model.error.contains("exactly one"), "Multiple items rejected")
        require(!state.receive([NSItemProvider(object: "not a file" as NSString)], model: model, map: map), "Non-file item rejected")
        for raw: Any in ["https://example.invalid/image.png", "file://server/private.png", Data("not a file URL".utf8)] {
            do { _ = try NativeImageDropState.localURL(raw); preconditionFailure("Nonlocal input accepted") } catch { }
        }
        model.error = ""
        require(state.receive([item], model: model, map: map), "Actual NSItemProvider accepted")
        try await settle()
        require(model.personal[map.id]?.image != nil, "Actual file URL provider callback prepared the image")
        let beforeSave = try await backend.call("fixture.counts")
        require(beforeSave["annotations.save"] == nil, "Drop never auto-saves")
        await model.savePersonal(map)
        let afterSave = try await backend.call("fixture.counts")
        require(afterSave["annotations.save"] as? Int == 1 && model.personal[map.id] == nil, "One explicit Save persists selected copy")
        require(afterSave["history.record"] == nil && afterSave["snapshot"] as? Int == 1, "Image edit does not alter history or reset library")
        print("Image drop checks passed: exact local single-item choice, pre-draft stale guards, newer picker/package intent, invalid-image retention, actual NSItemProvider and explicit-only save")
    }
}
