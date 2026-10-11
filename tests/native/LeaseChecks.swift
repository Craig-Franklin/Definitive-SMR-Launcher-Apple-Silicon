import Foundation
import Darwin

@main struct LeaseChecks {
    static func main() {
        do {
            let owner = InstanceLease.Owner(pid: ProcessInfo.processInfo.processIdentifier,
                launched: Date(), executable: CommandLine.arguments[0])
            let lease = try InstanceLease(url: URL(fileURLWithPath: CommandLine.arguments[1]), owner: owner)
            withExtendedLifetime(lease) {
                print("owned:\(owner.pid)"); fflush(stdout)
                _ = readLine()
            }
        } catch InstanceLease.Failure.occupied(let owner) {
            print("occupied:\(owner?.pid.description ?? "unknown")")
            exit(2)
        } catch { print("unavailable"); exit(3) }
    }
}
