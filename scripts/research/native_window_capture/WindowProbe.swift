import AppKit
import CoreGraphics
import ImageIO
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}
func emit(_ value: Any) {
    let bytes = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(bytes + Data([10]))
}
// Keep this projection separate from the API call so privacy/error controls use
// the exact production serializer without querying any desktop windows.
func pidWindowMetadata(_ rows: [[String: Any]]?, pid: Int32, access: Bool,
                       started: Double, finished: Double) -> [String: Any] {
    func number(_ value: Any?, integer: Bool = false) -> Any {
        guard let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
              n.doubleValue.isFinite,
              !integer || n.doubleValue.rounded() == n.doubleValue else { return NSNull() }
        return n
    }
    let windows: Any = rows.map { rows in rows.compactMap { row -> [String: Any]? in
        guard let owner = number(row[kCGWindowOwnerPID as String], integer: true) as? NSNumber,
              owner.doubleValue == Double(pid) else { return nil }
        var bounds: Any = NSNull()
        if let b = row[kCGWindowBounds as String] as? [String: Any] {
            bounds = ["x": number(b["X"]), "y": number(b["Y"]),
                      "width": number(b["Width"]), "height": number(b["Height"])]
        }
        let onscreen = row[kCGWindowIsOnscreen as String] as? NSNumber
        return ["window": number(row[kCGWindowNumber as String], integer: true), "pid": pid,
                "bounds": bounds, "layer": number(row[kCGWindowLayer as String], integer: true),
                "on_screen": onscreen.flatMap { CFGetTypeID($0) == CFBooleanGetTypeID() ? $0 : nil } as Any? ?? NSNull(),
                "alpha": number(row[kCGWindowAlpha as String]),
                "sharing": number(row[kCGWindowSharingState as String], integer: true)]
    }} as Any? ?? NSNull()
    return ["status": rows == nil ? "enumeration_error" : access ? "ok" : "permission_limited",
            "pid": pid, "started_unix_seconds": started, "finished_unix_seconds": finished,
            "screen_recording_authorized": access, "windows": windows]
}
let args = CommandLine.arguments
if args.count == 3 && args[1] == "enumerate-pid", let pid = Int32(args[2]), pid > 0 {
    let access = CGPreflightScreenCaptureAccess() // Check only; never request permission.
    let started = Date().timeIntervalSince1970
    let rows = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]]
    emit(pidWindowMetadata(rows, pid: pid, access: access,
                           started: started, finished: Date().timeIntervalSince1970))
    exit(rows == nil ? 2 : 0)
}
if args.count == 3 && args[1] == "inspect", let id = UInt32(args[2]), id > 0 {
    guard let rows = CGWindowListCopyWindowInfo([.optionOnScreenAboveWindow,.optionIncludingWindow], id) as? [[String: Any]] else { fail("Window metadata unavailable") }
    let matches = rows.filter { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value == id }
    if matches.isEmpty {
        FileHandle.standardError.write(("Exact window absent\n").data(using: .utf8)!)
        exit(3)
    }
    guard matches.count == 1, let row = matches.first,
          let found = row[kCGWindowNumber as String] as? NSNumber,
          found.uint32Value == id,
          let owner = row[kCGWindowOwnerPID as String] as? NSNumber,
          let bounds = row[kCGWindowBounds as String] as? [String: Any],
          let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { fail("Exact window ambiguous or malformed") }
    emit(["window": id, "pid": owner.intValue, "bounds": ["x":rect.minX,"y":rect.minY,"width":rect.width,"height":rect.height], "layer":row[kCGWindowLayer as String] ?? NSNull()])
    exit(0)
}
if args.count == 3 && args[1] == "image" {
    let url = URL(fileURLWithPath: args[2])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(source) == 1,
          let properties = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [String:Any],
          let w = properties[kCGImagePropertyPixelWidth as String] as? Int,
          let h = properties[kCGImagePropertyPixelHeight as String] as? Int,
          w > 0, h > 0, w <= 4096, h <= 4096,
          let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCache:true] as CFDictionary), image.width == w, image.height == h else { fail("Invalid bounded image") }
    let width = image.width, height = image.height
    guard let color = CGColorSpace(name: CGColorSpace.sRGB) else { fail("No sRGB") }
    var pixels = [UInt8](repeating: 0, count: width * height * 4)
    let sample: [[Int]] = pixels.withUnsafeMutableBytes { buffer in
        guard let ctx = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4, space: color, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { fail("No pixel context") }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return [(width/4,height/2),(3*width/4,height/2)].map { x,y in
            let start = (y * width + x) * 4
            return (0..<4).map { Int(buffer[start+$0]) }
        }
    }
    emit(["width":width,"height":height,"left_right_rgba":sample]);exit(0)
}
if args.count == 2 && args[1] == "fixture" {
    final class Paint: NSView {
        let kind: String
        init(frame: NSRect, kind: String) { self.kind = kind; super.init(frame:frame) }
        required init?(coder: NSCoder) { fatalError("No coder") }
        override func draw(_ dirtyRect: NSRect) {
            (kind == "A" ? NSColor.red : NSColor.green).setFill()
            NSBezierPath(rect: NSRect(x:0,y:0,width:bounds.width/2,height:bounds.height)).fill()
            (kind == "A" ? NSColor.blue : NSColor.yellow).setFill()
            NSBezierPath(rect: NSRect(x:bounds.width/2,y:0,width:bounds.width/2,height:bounds.height)).fill()
            for stripe in 0..<16 {
                (stripe % 2 == 0 ? NSColor.black : NSColor.white).setFill()
                NSBezierPath(rect:NSRect(x:CGFloat(30+stripe*2),y:bounds.height-50,width:2,height:16)).fill()
            }
            "native 0123456789".draw(at:NSPoint(x:80,y:bounds.height-48),withAttributes:[.font:NSFont.systemFont(ofSize:8),.foregroundColor:NSColor.black])
            let label = "ROOT OWNED CAPTURE PROOF " + kind
            label.draw(at:NSPoint(x:30,y:30), withAttributes:[.font:NSFont.systemFont(ofSize:30),.foregroundColor:NSColor.black])
        }
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    var windows = [NSWindow]()
    for (kind,rect) in [("A",NSRect(x:40,y:100,width:1200,height:700)),("B",NSRect(x:100,y:180,width:640,height:420))] {
        let w = NSWindow(contentRect:rect,styleMask:[.titled],backing:.buffered,defer:false)
        w.title = "Root Capture Proof " + kind
        w.setFrame(rect,display:true)
        w.contentView = Paint(frame:w.contentView!.bounds,kind:kind)
        w.isReleasedWhenClosed = false
        w.orderFrontRegardless()
        windows.append(w)
    }
    app.activate(ignoringOtherApps:true)
    DispatchQueue.main.asyncAfter(deadline:.now()+0.3) {
        emit(["pid":ProcessInfo.processInfo.processIdentifier,"windows":zip(["A","B"],windows).map { ["kind":$0.0,"window":$0.1.windowNumber] as [String:Any] }])
    }
    Timer.scheduledTimer(withTimeInterval:120,repeats:false) { _ in app.terminate(nil) }
    withExtendedLifetime(windows) { app.run() }
    exit(0)
}
fail("Usage: WindowProbe enumerate-pid PID | inspect ID | image PNG | fixture")
