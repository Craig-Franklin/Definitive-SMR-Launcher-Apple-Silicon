// Original local research helper. Reads a screenshot; never controls an app.
import Foundation
import Vision
import AppKit
import CoreGraphics

let arguments = Array(CommandLine.arguments.dropFirst())
let mode = arguments.count == 2 ? arguments[0] : nil
let imagePath = arguments.last ?? ""
let validMode = mode == nil || mode == "--bottom-controls" || mode == "--hud-strip" || mode == "--hud-strip-3x" || mode == "--setup-3x"

guard validMode && (arguments.count == 1 || arguments.count == 2) && !imagePath.isEmpty,
      let image = NSImage(contentsOfFile: imagePath),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Usage: read_screen_text [--bottom-controls|--hud-strip|--hud-strip-3x|--setup-3x] screenshot.png\n", stderr)
    exit(2)
}

func recognize(_ image: CGImage, region: CGRect?, languageCorrection: Bool = false) throws -> [[String: Any]] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = languageCorrection
    if let region {
        request.minimumTextHeight = 0.005
        request.regionOfInterest = region
    }
    try VNImageRequestHandler(cgImage: image).perform([request])
    return (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let rect = observation.boundingBox
        // Vision ROI coordinates are normalized within the region. Restore
        // full-image coordinates so the driver can apply the same UI bounds.
        let minX = region.map { $0.minX + rect.minX * $0.width } ?? rect.minX
        let minY = region.map { $0.minY + rect.minY * $0.height } ?? rect.minY
        let width = region.map { rect.width * $0.width } ?? rect.width
        let height = region.map { rect.height * $0.height } ?? rect.height
        return ["text": candidate.string, "confidence": candidate.confidence,
                "x": minX, "y": 1 - (minY + height),
                "width": width, "height": height]
    }
}

func enlarged(_ source: CGImage, factor: CGFloat) -> CGImage? {
    let width = Int(CGFloat(source.width) * factor)
    let height = Int(CGFloat(source.height) * factor)
    guard let context = CGContext(data: nil, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
    context.interpolationQuality = .high
    context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
    return context.makeImage()
}

do {
    let results: [[String: Any]]
    switch mode {
    case "--setup-3x":
        guard let scaled = enlarged(cg, factor: 3) else {
            fputs("Could not create enlarged screenshot\n", stderr)
            exit(1)
        }
        // Keep language correction off for custom scenario names. The caller
        // still requires its exact expected title and setup controls.
        results = try recognize(scaled, region: nil)
    case "--bottom-controls":
        let region = CGRect(x: 0, y: 0, width: 1, height: 0.35)
        results = try recognize(cg, region: region)
    case "--hud-strip":
        // Read the currency and date boxes separately. The horizontal gap
        // excludes the clock icon; the date crop ends before the speed buttons.
        // Vision uses a bottom-left origin; these y values cover the top 9%.
        let currency = CGRect(x: 0.69, y: 0.91, width: 0.075, height: 0.09)
        let date = CGRect(x: 0.82, y: 0.91, width: 0.10, height: 0.09)
        results = try recognize(cg, region: currency, languageCorrection: true)
            + recognize(cg, region: date, languageCorrection: true)
    case "--hud-strip-3x":
        // A single bounded retry for small captures. Vision still uses normalized
        // ROIs, so returned boxes remain in original screenshot coordinates.
        guard let scaled = enlarged(cg, factor: 3) else {
            fputs("Could not create enlarged screenshot\n", stderr)
            exit(1)
        }
        let currency = CGRect(x: 0.69, y: 0.91, width: 0.075, height: 0.09)
        let date = CGRect(x: 0.82, y: 0.91, width: 0.10, height: 0.09)
        results = try recognize(scaled, region: currency, languageCorrection: true)
            + recognize(scaled, region: date, languageCorrection: true)
    default:
        results = try recognize(cg, region: nil)
    }
    let data = try JSONSerialization.data(withJSONObject: results, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    fputs("Screenshot text recognition failed: \(error)\n", stderr)
    exit(1)
}
