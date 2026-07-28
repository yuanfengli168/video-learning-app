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

// MARK: - /api/health response

/// Response shape for `GET /api/health`. Used to detect whether the AI
/// tutor (Ollama) is reachable from the iOS app. `nil` from
/// `APIClient.fetchHealth()` means the entire backend is unreachable
/// (network error); when `nil` is returned, the caller should treat the
/// tutor as offline AND the API as unreachable.
struct HealthStatus: Codable {
    let status: String           // "ok"
    let app: String              // app name
    let ollama: OllamaStatus

    struct OllamaStatus: Codable {
        let available: Bool
        let detail: String
        let model: String
    }
}
