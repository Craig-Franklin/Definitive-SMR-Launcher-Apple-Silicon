import Darwin
import Foundation

// Cooperating normal frontends share one lifetime lock. Fixture previews never
// acquire it. The file stays in place: deleting it would allow two lock owners.
final class InstanceLease {
    struct Owner: Codable {
        let pid: Int32
        let launched: Date
        let executable: String
    }
    enum Failure: Error {
        case occupied(Owner?)
        case unavailable
    }
    private let descriptor: Int32

    init(url: URL, owner: Owner) throws {
        let fd = Darwin.open(url.path, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw Failure.unavailable }
        var retained = false
        defer { if !retained { Darwin.close(fd) } }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_nlink == 1,
              info.st_mode & 0o777 == 0o600 else { throw Failure.unavailable }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            guard errno == EWOULDBLOCK else { throw Failure.unavailable }
            var bytes = [UInt8](repeating: 0, count: 4097)
            let count = pread(fd, &bytes, bytes.count, 0)
            let existing = count > 0 && count <= 4096
                ? try? JSONDecoder().decode(Owner.self, from: Data(bytes.prefix(count))) : nil
            throw Failure.occupied(existing)
        }
        let data = try JSONEncoder().encode(owner)
        guard data.count <= 4096, ftruncate(fd, 0) == 0 else { throw Failure.unavailable }
        let written = data.withUnsafeBytes { pwrite(fd, $0.baseAddress, $0.count, 0) }
        guard written == data.count else { throw Failure.unavailable }
        descriptor = fd
        retained = true
    }
    deinit { Darwin.close(descriptor) }
}
