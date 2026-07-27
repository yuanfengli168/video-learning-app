import Foundation

/// Result of a `/m/snapshot` call.
///
/// `notModified == true` means the server returned 304 — nothing changed
/// since the last ETag. The phone just bumps `lastSyncDate` and does
/// NOT replace its `snapshot` (the previous response is still canonical).
struct SnapshotResult {
    let snapshot: Snapshot
    let etag: String
    let notModified: Bool
}
