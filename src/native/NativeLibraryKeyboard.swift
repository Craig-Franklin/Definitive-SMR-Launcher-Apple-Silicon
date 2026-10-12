import Foundation

enum LibraryMove { case left, right, up, down }

// Match the adaptive grid's minimum, spacing and horizontal content padding.
enum NativeLibraryKeyboard {
    static let minimumWidth: CGFloat = 155
    static let spacing: CGFloat = 16
    static let padding: CGFloat = 16

    static func columns(in width: CGFloat) -> Int {
        max(1, Int(max(0, width - 2 * padding + spacing) / (minimumWidth + spacing)))
    }

    static func nextIndex(count: Int, selected: Int?, columns: Int, move: LibraryMove) -> Int? {
        guard count > 0 else { return nil }
        guard let index = selected, (0..<count).contains(index) else { return 0 }
        let stride = max(1, columns)
        switch move {
        case .left: return max(0, index - 1)
        case .right: return min(count - 1, index + 1)
        case .up: return index < stride ? index : index - stride
        case .down:
            return index / stride == (count - 1) / stride ? index : min(count - 1, index + stride)
        }
    }
}
