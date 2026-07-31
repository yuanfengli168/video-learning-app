import Foundation

/// Sort direction for the video list inside each section. Persisted via
/// @AppStorage so the user's choice survives across view rebuilds and
/// app restarts.
///
/// MVP0.2 followup #7: user feedback was "videos inside each section
/// should sort by title name (asc/desc), not by the backend's orderIndex".
/// The toggle is in the same toolbar menu as the section sort, with the
/// same persistence pattern.
enum VideoSortOrder: String, CaseIterable, Identifiable {
    case ascending = "asc"
    case descending = "desc"
    var id: String { rawValue }
    var label: String {
        switch self {
        case .ascending:  return "Title A \u{2192} Z"
        case .descending: return "Title Z \u{2192} A"
        }
    }
    var systemImage: String {
        switch self {
        case .ascending:  return "arrow.up"
        case .descending: return "arrow.down"
        }
    }
    /// Menu label for the picker section. Keeps the menu readable
    /// when two sort pickers are stacked (sections + videos).
    var menuLabel: String { "Title" }
}

/// Natural-sort extension for String. Used by Video.title comparison so
/// leading numbers sort numerically, not lexicographically.
///
/// Why: titles like "1.-AI\u2026", "2.-AI\u2026", "10.-AI\u2026" include
/// leading numbers. With plain string compare, "10" sorts before "2"
/// because "1" < "2" lexicographically. With natural sort, "1" < "2" <
/// "10" numerically.
///
/// CJK + non-ASCII titles fall through to `localizedCaseInsensitiveCompare`
/// which handles locale-aware collation (so Chinese pinyin order works
/// reasonably without us doing custom dictionary lookups).
extension String {
    /// Returns a natural-sort key tuple: (leading_number_or_inf, lowercased_string).
    /// Two strings with different leading numbers sort numerically first;
    /// strings with the same leading number fall back to alphabetical
    /// (case-insensitive, locale-aware).
    func naturalSortKey() -> (Int, String) {
        let leadingNumber = self.leadingNumber() ?? Int.max
        return (leadingNumber, self.lowercased())
    }

    /// Extract the leading integer from the start of the string, if any.
    /// Matches negative-free integers with optional whitespace and a
    /// trailing period/space/dash (e.g. "1.-", "1 ", "23.-", "100 ").
    /// Returns nil if no leading number is found.
    private func leadingNumber() -> Int? {
        // Strip leading whitespace, then parse digits until non-digit.
        let trimmed = self.drop(while: { $0 == " " || $0 == "\t" })
        var digits = ""
        for ch in trimmed {
            if ch.isNumber {
                digits.append(ch)
            } else {
                break
            }
        }
        return digits.isEmpty ? nil : Int(digits)
    }
}

/// Convenience for sorting arrays of items with a `title: String` field.
extension Sequence where Element == Video {
    /// Sort videos by title using natural-sort order (numeric prefix
    /// first, then alphabetical).
    func sortedByTitle(natural order: VideoSortOrder) -> [Video] {
        let arr = self.sorted { lhs, rhs in
            let lk = lhs.title.naturalSortKey()
            let rk = rhs.title.naturalSortKey()
            if lk.0 != rk.0 { return order == .ascending ? lk.0 < rk.0 : lk.0 > rk.0 }
            return order == .ascending ? lk.1 < rk.1 : lk.1 > rk.1
        }
        return arr
    }
}
