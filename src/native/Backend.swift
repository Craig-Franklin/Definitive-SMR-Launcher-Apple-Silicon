import AppKit
import Foundation

// The Python process retains service and transaction authority. A lost connection
// never causes a mutation to be retried: its result may be unknown.
@MainActor final class Backend {
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var buffer = Data()
    private var pending: [String: CheckedContinuation<[String: Any], Error>] = [:]
    var event: ((String, [String: Any]) -> Void)?
    var stopped: ((String) -> Void)?

    func start(executable: URL? = nil, arguments: [String] = ["--native-service"]) throws {
        let candidate = executable ?? Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/Definitive SMR Launcher Apple Silicon")
        guard FileManager.default.isExecutableFile(atPath: candidate.path) else {
            throw NativeError.message("The bundled launcher services are missing. Reinstall the complete application.")
        }
        let child = Process(), requests = Pipe(), replies = Pipe()
        child.executableURL = candidate
        child.arguments = arguments
        child.standardInput = requests
        child.standardOutput = replies
        // Keep diagnostics out of the protocol and public reports.
        child.standardError = FileHandle.standardError
        input = requests.fileHandleForWriting
        output = replies.fileHandleForReading
        output?.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            DispatchQueue.main.async { self?.receive(chunk) }
        }
        child.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.disconnect("Launcher services stopped. An unfinished action may have completed; inspect its result before trying again.") }
        }
        process = child
        try child.run()
    }

    func call(_ method: String, _ params: [String: Any] = [:]) async throws -> [String: Any] {
        guard let input, process?.isRunning == true else {
            throw NativeError.message("Launcher services are unavailable. Reopen the app after inspecting any unfinished action.")
        }
        let id = UUID().uuidString
        var data = try JSONSerialization.data(withJSONObject: ["id": id, "method": method, "params": params])
        data.append(10)
        let payload = data
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                do { try input.write(contentsOf: payload) }
                catch { Task { @MainActor in self?.disconnect("Could not contact launcher services. The action result is unknown.") } }
            }
        }
    }

    private func receive(_ chunk: Data) {
        guard !chunk.isEmpty else {
            disconnect("Launcher connection closed. An unfinished action result is unknown.")
            return
        }
        buffer.append(chunk)
        guard buffer.count <= 128 * 1024 * 1024 else {
            disconnect("Launcher response exceeds its size limit.")
            return
        }
        while let end = buffer.firstIndex(of: 10) {
            let line = buffer.prefix(upTo: end)
            buffer.removeSubrange(...end)
            do {
                guard let object = try JSONSerialization.jsonObject(with: line) as? [String: Any] else {
                    throw NativeError.message("Invalid launcher response.")
                }
                if let name = object["event"] as? String {
                    event?(name, object["data"] as? [String: Any] ?? [:])
                } else if let id = object["id"] as? String, let continuation = pending.removeValue(forKey: id) {
                    if object["ok"] as? Bool == true {
                        continuation.resume(returning: object["data"] as? [String: Any] ?? [:])
                    } else {
                        continuation.resume(throwing: NativeError.message(object["error"] as? String ?? "The operation failed."))
                    }
                } else {
                    throw NativeError.message("Unmatched launcher response. Inspect the unfinished operation.")
                }
            } catch { disconnect(error.localizedDescription); return }
        }
    }

    private func disconnect(_ message: String) {
        output?.readabilityHandler = nil
        try? input?.close()
        input = nil
        let waiting = pending.values
        pending.removeAll()
        for continuation in waiting { continuation.resume(throwing: NativeError.message(message)) }
        stopped?(message)
    }
}

extension Notification.Name { static let nativeServicesClosed = Notification.Name("SMRNativeServicesClosed") }

enum NativeError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { return value }; return nil }
}

extension Dictionary where Key == String, Value == Any {
    func text(_ key: String, _ fallback: String = "") -> String { self[key] as? String ?? fallback }
    func flag(_ key: String) -> Bool { self[key] as? Bool ?? false }
    func object(_ key: String) -> [String: Any] { self[key] as? [String: Any] ?? [:] }
    func rows(_ key: String) -> [[String: Any]] { self[key] as? [[String: Any]] ?? [] }
}
