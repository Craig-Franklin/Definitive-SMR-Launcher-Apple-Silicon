// Exact image correspondence only. The caller owns scene semantics and runtime admission.
import CoreGraphics
import Darwin
import Foundation

enum MatchRefusal: String, Error {
  case geometryChanged = "geometry_changed"
  case referenceInvalid = "reference_invalid"
  case matchMissing = "match_missing"
  case matchAmbiguous = "match_ambiguous"
}

func integer(_ value: String) throws -> Int {
  guard let number = Int(value), String(number) == value else { throw Refusal.arguments }
  return number
}

func rgbaPixels(_ image: CGImage) throws -> [UInt8] {
  var bytes = [UInt8](repeating: 0, count: image.width * image.height * 4)
  guard let space = CGColorSpace(name: CGColorSpace.sRGB) else { throw Refusal.imageInvalid }
  let rendered = bytes.withUnsafeMutableBytes { storage in
    guard let context = CGContext(
      data: storage.baseAddress, width: image.width, height: image.height,
      bitsPerComponent: 8, bytesPerRow: image.width * 4, space: space,
      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
    ) else { return false }
    context.interpolationQuality = .none
    context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    return true
  }
  guard rendered else { throw Refusal.imageInvalid }
  return bytes
}

func correspondence(
  reference: CGImage, target: CGImage, rectangle: [Int], offset: [Double]
) throws -> [String: Any] {
  let width = reference.width, height = reference.height
  guard target.width == width, target.height == height else { throw MatchRefusal.geometryChanged }
  guard rectangle.count == 4, offset.count == 2 else { throw Refusal.arguments }
  let x = rectangle[0], y = rectangle[1], w = rectangle[2], h = rectangle[3]
  guard x >= 0, y >= 0, (4...128).contains(w), (4...128).contains(h),
    x <= width - w, y <= height - h,
    offset.allSatisfy({ $0.isFinite }), offset[0] >= 0, offset[0] < Double(w),
    offset[1] >= 0, offset[1] < Double(h)
  else { throw MatchRefusal.referenceInvalid }
  let source = try rgbaPixels(reference), current = try rgbaPixels(target)
  let stride = width * 4
  var colors = Set<UInt32>()
  for row in y..<(y + h) {
    for column in x..<(x + w) {
      let i = row * stride + column * 4
      colors.insert(
        UInt32(source[i]) << 24 | UInt32(source[i + 1]) << 16
          | UInt32(source[i + 2]) << 8 | UInt32(source[i + 3]))
    }
  }
  guard colors.count > 1 else { throw MatchRefusal.referenceInvalid }
  var found: [Int]?
  try source.withUnsafeBufferPointer { original in
    try current.withUnsafeBufferPointer { image in
      guard let originalBase = original.baseAddress, let imageBase = image.baseAddress else {
        throw Refusal.imageInvalid
      }
      for cy in 0...(height - h) {
        for cx in 0...(width - w) {
          var equal = true
          for row in 0..<h {
            if memcmp(
              originalBase + (y + row) * stride + x * 4,
              imageBase + (cy + row) * stride + cx * 4, w * 4
            ) != 0 {
              equal = false
              break
            }
          }
          if equal {
            guard found == nil else { throw MatchRefusal.matchAmbiguous }
            found = [cx, cy]
          }
        }
      }
    }
  }
  guard let found else { throw MatchRefusal.matchMissing }
  return [
    "width": width, "height": height,
    "rectangle": [found[0], found[1], w, h],
    "point": [Double(found[0]) + offset[0], Double(found[1]) + offset[1]],
    "claim": "image_correspondence_only", "runtime_authority_granted": false,
  ]
}

@main
struct ImageControlMatch {
  static func main() {
    do {
      let args = CommandLine.arguments
      guard args.count == 11, let dx = Double(args[9]), let dy = Double(args[10]) else {
        throw Refusal.arguments
      }
      let reference = try decodePNG(readOwned(args[1], expectedHash: args[2]))
      let target = try decodePNG(readOwned(args[3], expectedHash: args[4]))
      let rectangle = try args[5...8].map(integer)
      var result = try correspondence(
        reference: reference, target: target, rectangle: rectangle, offset: [dx, dy])
      result["schema"] = "smr-image-control-correspondence/1"
      result["reference_sha256"] = args[2]
      result["input_sha256"] = args[4]
      let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
      guard data.count + 1 <= outputLimit else { throw Refusal.outputLimit }
      FileHandle.standardOutput.write(data + Data([10]))
    } catch {
      let code = (error as? MatchRefusal)?.rawValue ?? (error as? Refusal)?.rawValue ?? "refused"
      FileHandle.standardError.write(Data(("smr-image-control-correspondence/1: " + code + "\n").utf8))
      exit(1)
    }
  }
}
