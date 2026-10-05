// Original local research helper. Reads a screenshot; never controls an app.
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count == 2,
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Usage: read_screen_text screenshot.png\n", stderr)
    exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["en-US"]
request.usesLanguageCorrection = false
do {
    try VNImageRequestHandler(cgImage: cg).perform([request])
    let results: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let rect = observation.boundingBox
        return ["text": candidate.string, "confidence": candidate.confidence,
                "x": rect.minX, "y": 1 - rect.maxY,
                "width": rect.width, "height": rect.height]
    }
    let data = try JSONSerialization.data(withJSONObject: results, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    fputs("Screenshot text recognition failed: \(error)\n", stderr)
    exit(1)
}
