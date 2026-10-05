// Local-only Apple Translation provider. Compile with the macOS 26 SDK or newer.
// The deployment target may be macOS 13; the framework and API are availability guarded.
import Foundation
import Translation

struct Request: Decodable {
    let text: String
    let source: String
    let target: String
}
struct Response: Encodable {
    var text: String? = nil
    var error: String? = nil
}

@main struct LauncherTranslation {
    static func output(_ response: Response) {
        if let data = try? JSONEncoder().encode(response) {
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data([10]))
        }
    }

    static func main() async {
        guard #available(macOS 26.0, *) else {
            output(Response(error: "Apple briefing translation requires macOS 26 or later."))
            return
        }
        do {
            let data = FileHandle.standardInput.readDataToEndOfFile()
            guard data.count <= 1_000_000 else {
                output(Response(error: "This briefing is too large to translate."))
                return
            }
            let request = try JSONDecoder().decode(Request.self, from: data)
            guard !request.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                output(Response(text: request.text))
                return
            }
            if request.source == request.target {
                output(Response(text: request.text))
                return
            }
            let source = Locale.Language(identifier: request.source)
            let target = Locale.Language(identifier: request.target)
            let availability = LanguageAvailability()
            switch await availability.status(from: source, to: target) {
            case .unsupported:
                output(Response(error: "Apple Translation does not support this language pair on this Mac."))
            case .supported:
                output(Response(error: "Download both languages in System Settings → General → Language & Region → Translation Languages, then try again. Apple downloads its language models; the briefing stays on this Mac."))
            case .installed:
                let session = TranslationSession(installedSource: source, target: target)
                let response = try await session.translate(request.text)
                output(Response(text: response.targetText))
            @unknown default:
                output(Response(error: "Apple Translation could not determine language availability."))
            }
        } catch {
            // Avoid exposing the user's briefing through diagnostics.
            output(Response(error: "Apple translation could not complete. Check installed Translation Languages in System Settings."))
        }
    }
}
