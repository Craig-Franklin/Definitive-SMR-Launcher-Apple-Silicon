import AppKit
import Foundation

@main struct ModelChecks {
    @MainActor static func main() async throws {
        let model = LauncherModel(fixture: true)
        model.maps = try (0..<1000).map { index in
            try MapItem(["id": "fixture-\(index)", "name": String(format: "Map %04d", index),
                "archive_sha256": String(repeating: "a", count: 64), "variant_id": String(repeating: "b", count: 64),
                "metadata": ["author": index % 2 == 0 ? "Cedar" : "Coast", "map_type": "Single player"],
                "verification": "Not verified", "summary": ["light": index == 0 ? "amber" : "grey"]])
        }
        let chosen = model.maps[25]
        model.select(chosen)
        model.query = "Cedar"
        precondition(model.visible.count == 500)
        precondition(model.selection == chosen.id, "Search must retain hidden selection")
        model.query = ""; model.filter = "Known issue"
        precondition(model.visible.map(\.id) == ["fixture-0"], "Unknowns are not known issues")
        model.filter = "All maps"; model.sort = "Name Z–A"
        precondition(model.visible.first?.id == "fixture-999")
        model.edit(chosen)
        model.personal[chosen.id]?.notes = "Retained synthetic draft"
        model.personal[chosen.id]?.dirty = true
        model.destination = "Preferences"; model.destination = "Map Library"
        model.select(model.maps[30]); model.select(chosen)
        precondition(model.personal[chosen.id]?.notes == "Retained synthetic draft")
        precondition(model.dirty)
        await model.loadDetails(chosen.id)
        model.record(chosen)
        precondition(model.outcomes[model.outcomeKey(chosen)]?.attempt == "fictional-attempt")
        model.outcomes[model.outcomeKey(chosen)]?.outcome = "Saved"
        await model.confirm(chosen)
        precondition(model.error.contains("Choose the exact save"))
        precondition(model.outcomes[model.outcomeKey(chosen)] != nil)
        let legacyIssue = try MapItem(["id": "legacy", "archive_sha256": "a", "variant_id": "b", "verification": "Known issue", "summary": ["label": "Never opened"]])
        precondition(legacyIssue.status == "Known issue · Never opened")
        await model.savePersonal(chosen)
        precondition(model.personal[chosen.id] != nil, "Fixture must never report a persisted save")
        var elapsed: [Double] = []
        for index in 0..<40 {
            let start = ContinuousClock.now
            model.query = index % 2 == 0 ? "Cedar" : "Map"
            model.sort = index % 2 == 0 ? "Name A–Z" : "Name Z–A"
            _ = model.visible
            let duration = start.duration(to: .now).components
            elapsed.append(Double(duration.seconds) * 1000 + Double(duration.attoseconds) / 1e15)
        }
        let p95 = elapsed.sorted()[37]
        precondition(p95 < 250, "In-memory filtering/sorting regressed")
        print(String(format: "Native model checks passed; 1000 synthetic maps, 40 in-memory queries, p95 %.2f ms (not render timing)", p95))

        try await checkDecoder()

        let backend = Backend()
        var eventSeen = false
        backend.event = { name, data in eventSeen = name == "fixture" && data.flag("synthetic") }
        try backend.start(executable: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [CommandLine.arguments[2]])
        let reply = try await backend.call("echo", ["text": "Unicode café · 地图"])
        precondition(reply.text("text") == "Unicode café · 地图" && eventSeen)
        let firstText = String(repeating: "a", count: 100_000)
        let secondText = String(repeating: "b", count: 100_000)
        async let firstReply = backend.call("echo", ["text": firstText])
        async let secondReply = backend.call("echo", ["text": secondText])
        let concurrent = try await (firstReply, secondReply)
        precondition(concurrent.0.text("text") == firstText && concurrent.1.text("text") == secondText, "Concurrent requests must retain separate ordered wire envelopes")
        do { _ = try await backend.call("reject"); preconditionFailure("Service failure was swallowed") }
        catch { precondition(error.localizedDescription == "synthetic rejection") }
        let liveModel = LauncherModel(fixture: false, backend: backend)
        liveModel.connected = true
        var ticks = 0
        var longestGap = 0.0
        let clock = ContinuousClock()
        var previous = clock.now
        let heartbeat = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000)
                let now = clock.now
                let duration = previous.duration(to: now).components
                longestGap = max(longestGap, Double(duration.seconds) * 1000 + Double(duration.attoseconds) / 1e15)
                previous = now; ticks += 1
            }
        }
        await Task.yield()
        let loadStarted = clock.now
        await liveModel.refresh()
        heartbeat.cancel()
        await heartbeat.value
        precondition(liveModel.error.isEmpty && liveModel.maps.count == 1000)
        precondition(ticks > 1 && longestGap < 500, "Large fragmented replies must not monopolize the main actor")
        print(String(format: "Large synthetic library passed; 1000 maps, 16 MiB text, %d main-actor ticks, max gap %.2f ms, total %@ (not viewport timing)", ticks, longestGap, String(describing: loadStarted.duration(to: clock.now))))

        let reported = liveModel.maps[0], untouched = liveModel.maps[1]
        liveModel.selection = reported.id; liveModel.query = "Synthetic"; liveModel.sort = "Name Z–A"
        _ = try await backend.call("fixture.configure", ["verification": "Verified"])
        await liveModel.loadDetails(reported.id, force: true)
        precondition(liveModel.maps[0].verification == "Verified")
        liveModel.filter = "Verified"
        precondition(liveModel.visible.map(\.id) == [reported.id], "Prime the Verified filter before evidence changes")
        _ = try await backend.call("fixture.configure", ["verification": "Not verified"])
        await liveModel.loadDetails(reported.id, force: true)
        precondition(liveModel.maps[0].verification == "Not verified", "Fresh detail evidence must withdraw a stale badge")
        precondition(liveModel.maps[1].verification == untouched.verification, "Detail evidence must not change sibling maps")
        precondition(liveModel.visible.isEmpty, "Withdrawn verification must also leave the Verified filter")
        liveModel.filter = "All maps"
        _ = try await backend.call("fixture.configure", ["verification": "Needs inspection", "changed_edition": true])
        await liveModel.loadDetails(reported.id, force: true)
        precondition(liveModel.maps[0].verification == "Not verified", "Wrong-edition detail evidence must not replace the badge")
        _ = try await backend.call("fixture.configure", ["verification": "Needs inspection", "changed_edition": false])
        await liveModel.loadDetails(reported.id, force: true)
        precondition(liveModel.maps[0].verification == "Needs inspection")
        _ = try await backend.call("fixture.configure", ["verification": "Not verified"])
        await liveModel.loadDetails(reported.id, force: true)
        liveModel.personal[untouched.id] = PersonalDraft(title: "Retain", notes: "Unsaved", dirty: true)
        liveModel.record(reported, attempt: "synthetic-attempt")
        liveModel.updateOutcome(reported) { $0.outcome = "Played"; $0.scenario = "Synthetic.xml" }
        let before = try await backend.call("fixture.counts")
        await liveModel.confirm(reported)
        let after = try await backend.call("fixture.counts")
        precondition(liveModel.error.isEmpty && liveModel.outcome(reported).outcome.isEmpty)
        precondition(after["snapshot"] as? Int == before["snapshot"] as? Int, "Recording one map must not rebuild the library")
        precondition((after["details"] as? Int ?? 0) == (before["details"] as? Int ?? 0) + 1, "Read the affected map once")
        precondition((after["history.record"] as? Int ?? 0) == 1, "Never repeat a confirmation")
        precondition(liveModel.maps[0].summary.text("label") == "Played" && liveModel.maps[1].summary.isEmpty)
        precondition(liveModel.personal[untouched.id]?.dirty == true && liveModel.maps.count == 1000)
        precondition(liveModel.selection == reported.id && liveModel.query == "Synthetic" && liveModel.sort == "Name Z–A")

        _ = try await backend.call("fixture.configure", ["changed_edition": true])
        await liveModel.loadDetails(reported.id, force: true)
        precondition(liveModel.maps[0].summary.text("label") == "Played", "Different editions cannot replace this map summary")

        _ = try await backend.call("fixture.configure", ["reject_record": true])
        liveModel.updateOutcome(reported) { $0.outcome = "Saved"; $0.save = String(repeating: "e", count: 64) }
        let beforeRejected = try await backend.call("fixture.counts")
        await liveModel.confirm(reported)
        let afterRejected = try await backend.call("fixture.counts")
        precondition(liveModel.error == "synthetic history.record failure" && !liveModel.outcome(reported).outcome.isEmpty)
        precondition(afterRejected["details"] as? Int == beforeRejected["details"] as? Int, "Rejected confirmations must retain drafts without a refresh")

        _ = try await backend.call("fixture.configure", ["reject_details": true])
        let beforeReadFailure = try await backend.call("fixture.counts")
        await liveModel.confirm(reported)
        let afterReadFailure = try await backend.call("fixture.counts")
        precondition(liveModel.error == "synthetic details failure" && liveModel.outcome(reported).outcome.isEmpty)
        precondition(liveModel.status.contains("confirmation recorded"), "Read failure must preserve the saved confirmation status")
        precondition((afterReadFailure["history.record"] as? Int ?? 0) == (beforeReadFailure["history.record"] as? Int ?? 0) + 1)
        precondition(afterReadFailure["snapshot"] as? Int == beforeReadFailure["snapshot"] as? Int)
        print("Confirmation checks passed; one detail read, no library rebuild, retained selection/drafts, rejected write and post-write read failure")

        liveModel.setTranslationLanguage(reported, \.target, "fr")
        let beforeTranslation = try await backend.call("fixture.counts")
        await liveModel.translateBriefing(reported)
        let afterTranslation = try await backend.call("fixture.counts")
        precondition(liveModel.error.isEmpty && liveModel.translation(reported).text == "Présentation fictive traduite.")
        precondition(liveModel.translation(reported).showingResult && liveModel.translation(untouched).text == nil)
        precondition(liveModel.maps[0].metadata.text("description") == reported.metadata.text("description"), "Translation must preserve the original briefing")
        precondition(afterTranslation["snapshot"] as? Int == beforeTranslation["snapshot"] as? Int, "Translation must not reload maps")
        precondition(afterTranslation["details"] as? Int == beforeTranslation["details"] as? Int, "Translation must not verify unrelated details")
        precondition(liveModel.personal[untouched.id]?.dirty == true && liveModel.selection == reported.id)
        liveModel.setTranslationLanguage(reported, \.source, "de")
        precondition(liveModel.translation(reported).text == nil && !liveModel.translation(reported).showingResult)
        _ = try await backend.call("fixture.configure", ["changed_translation_edition": true])
        await liveModel.translateBriefing(reported)
        precondition(liveModel.error.contains("no longer matches") && liveModel.translation(reported).text == nil)
        _ = try await backend.call("fixture.configure", ["reject_translation": true])
        await liveModel.translateBriefing(reported)
        precondition(liveModel.error == "synthetic translation unavailable" && liveModel.translation(reported).text == nil)
        _ = try await backend.call("fixture.configure", ["slow_translation": true])
        let translationTask = Task { await liveModel.translateBriefing(reported) }
        while !liveModel.busy { await Task.yield() }
        liveModel.select(untouched)
        precondition(liveModel.selection == untouched.id && liveModel.busy, "Cached selection stays usable during translation")
        await translationTask.value
        precondition(liveModel.translation(reported).text != nil && liveModel.translation(untouched).text == nil && liveModel.selection == untouched.id)
        print("Integrated translation checks passed; map-scoped result, unchanged original/library/drafts, rejected provider and wrong edition")
        try await checkPerformance(liveModel, backend)
        do { _ = try await backend.call("disconnect"); preconditionFailure("Lost mutation result was swallowed") }
        catch { precondition(error.localizedDescription.contains("unknown") || error.localizedDescription.contains("inspect")) }
        print("Native pipe checks passed; partial replies, events, explicit rejection and unknown disconnect")
    }

    static func checkDecoder() async throws {
        let bytes = Data("{\"text\":\"地图 café\"}\n{\"text\":\"second\"}\n{\"text\":\"third\"}\n".utf8)
        let split = bytes.firstIndex(of: 0xe5)! + 1
        let replies: [String] = try await withCheckedThrowingContinuation { continuation in
            var values: [String] = []
            let reader = ReplyDecoder { result in
                precondition(!Thread.isMainThread, "Framing and JSON decode must run off the UI thread")
                do {
                    values.append(try result.get().text("text"))
                    if values.count == 3 { continuation.resume(returning: values) }
                } catch { continuation.resume(throwing: error) }
            }
            reader.receive(bytes.prefix(split))
            reader.receive(bytes.suffix(from: split))
        }
        precondition(replies == ["地图 café", "second", "third"], "Split UTF-8 and coalesced replies must preserve wire order")
        for (data, message) in [(Data("[]\n".utf8), "Invalid launcher response"), (Data("{\n".utf8), ""), (Data(repeating: 65, count: 128 * 1024 * 1024 + 1), "size limit")] {
            await withCheckedContinuation { continuation in
                let reader = ReplyDecoder { result in
                    precondition(!Thread.isMainThread)
                    guard case .failure(let error) = result else { preconditionFailure("Invalid/oversized input was accepted") }
                    precondition(message.isEmpty || error.localizedDescription.contains(message), error.localizedDescription)
                    continuation.resume()
                }
                reader.receive(data)
            }
        }
        print("Background decoder checks passed; split UTF-8, ordered coalesced messages, malformed/nonobject JSON and 128 MiB cap")
    }
}
