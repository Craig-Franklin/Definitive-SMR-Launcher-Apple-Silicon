// Shared owned-PNG reader from the previously reviewed original image-text helper.
// Historical private snapshots stay unchanged; these functions retain their contract.
import CoreGraphics
import CryptoKit
import Darwin
import Foundation
import ImageIO

enum Refusal: String, Error {
  case arguments
  case inputInvalid = "input_invalid"
  case inputChanged = "input_changed"
  case hashMismatch = "hash_mismatch"
  case imageInvalid = "image_invalid"
  case recognitionFailed = "recognition_failed"
  case outputInvalid = "output_invalid"
  case outputLimit = "output_limit"
}

let inputLimit = 32 * 1024 * 1024
let outputLimit = 128 * 1024

func validHash(_ value: String) -> Bool {
  value.utf8.count == 64
    && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
}

func openPlain(_ path: String) throws -> Int32 {
  let parts = path.split(separator: "/", omittingEmptySubsequences: false)
  guard path.hasPrefix("/"), !path.utf8.contains(0), parts.count > 1,
    parts.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." })
  else { throw Refusal.inputInvalid }
  var parent = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
  guard parent >= 0 else { throw Refusal.inputInvalid }
  defer { Darwin.close(parent) }
  for part in parts.dropFirst().dropLast() {
    let next = String(part).withCString {
      openat(parent, $0, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
    }
    guard next >= 0 else { throw Refusal.inputInvalid }
    Darwin.close(parent)
    parent = next
  }
  let fd = String(parts.last!).withCString {
    openat(parent, $0, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
  }
  guard fd >= 0 else { throw Refusal.inputInvalid }
  return fd
}

func metadata(_ fd: Int32) throws -> stat {
  var value = stat()
  guard fstat(fd, &value) == 0,
    value.st_mode & S_IFMT == S_IFREG, value.st_uid == geteuid(), value.st_nlink == 1,
    value.st_size > 0, value.st_size <= inputLimit
  else { throw Refusal.inputInvalid }
  return value
}

func same(_ a: stat, _ b: stat) -> Bool {
  a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_mode == b.st_mode
    && a.st_uid == b.st_uid && a.st_gid == b.st_gid && a.st_nlink == b.st_nlink
    && a.st_size == b.st_size && a.st_flags == b.st_flags
    && a.st_mtimespec.tv_sec == b.st_mtimespec.tv_sec
    && a.st_mtimespec.tv_nsec == b.st_mtimespec.tv_nsec
    && a.st_ctimespec.tv_sec == b.st_ctimespec.tv_sec
    && a.st_ctimespec.tv_nsec == b.st_ctimespec.tv_nsec
    && a.st_birthtimespec.tv_sec == b.st_birthtimespec.tv_sec
    && a.st_birthtimespec.tv_nsec == b.st_birthtimespec.tv_nsec
}

#if TESTING
  // Compiled only into the synthetic test executable, never the shipped helper.
  nonisolated(unsafe) var afterReadForTest: ((Int32) throws -> Void)?
#endif

func readOwned(_ path: String, expectedHash: String) throws -> Data {
  guard validHash(expectedHash) else { throw Refusal.arguments }
  let fd = try openPlain(path)
  defer { Darwin.close(fd) }
  let before = try metadata(fd)
  var data = Data()
  var buffer = [UInt8](repeating: 0, count: 65536)
  while true {
    let count = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
    if count < 0 && errno == EINTR { continue }
    guard count >= 0 else { throw Refusal.inputInvalid }
    if count == 0 { break }
    guard data.count + count <= inputLimit else { throw Refusal.inputInvalid }
    data.append(contentsOf: buffer.prefix(count))
  }
  #if TESTING
    try afterReadForTest?(fd)
  #endif
  guard data.count == before.st_size, same(before, try metadata(fd))
  else { throw Refusal.inputChanged }
  // Resolve every component again: a renamed parent must not hide path replacement.
  let current = try openPlain(path)
  defer { Darwin.close(current) }
  guard same(before, try metadata(current)) else { throw Refusal.inputChanged }
  let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  guard hash == expectedHash else { throw Refusal.hashMismatch }
  return data
}

func decodePNG(_ data: Data) throws -> CGImage {
  guard data.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]),
    let source = CGImageSourceCreateWithData(
      data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
    CGImageSourceGetType(source) as String? == "public.png",
    CGImageSourceGetCount(source) == 1,
    CGImageSourceGetStatus(source) == .statusComplete,
    let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
    let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
    let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
    width.doubleValue.isFinite, height.doubleValue.isFinite,
    width.doubleValue == Double(width.intValue), height.doubleValue == Double(height.intValue),
    (1...8192).contains(width.intValue), (1...8192).contains(height.intValue),
    width.intValue * height.intValue <= 16 * 1024 * 1024,
    let image = CGImageSourceCreateImageAtIndex(
      source, 0, [kCGImageSourceShouldCache: false] as CFDictionary),
    image.width == width.intValue, image.height == height.intValue
  else { throw Refusal.imageInvalid }
  // ImageIO decoding is not the caller's strict PNG chunk/CRC/pixel validator.
  return image
}
