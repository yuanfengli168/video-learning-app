import Foundation
import Combine

/// Holds the last-synced snapshot in memory and exposes a `sync()` action
/// that fetches from the backend. The store is the single source of truth
/// for the UI: views read `snapshot` and never call the API directly.
@MainActor
final class SnapshotStore: ObservableObject {
    private static let lastETagKey = "pocket.lastETag"
    private static let snapshotCacheURL: URL = {
        // ~/Documents/snapshot_cache.json — survives app relaunch but is wiped
        // when the user uninstalls the app (iOS sandbox cleanup). For v0.1.2
        // this is the right scope: persistent across restarts, gone on uninstall.
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        return docs.appendingPathComponent("snapshot_cache.json")
    }()

    @Published var snapshot: Snapshot = .empty
    @Published var isSyncing: Bool = false
    @Published var lastError: String? = nil
    @Published var lastSyncDate: Date? = nil

    /// Per-video progress cache (in-memory; lost on relaunch — that's fine
    /// for v0.1, the server is authoritative and we re-fetch on demand).
    @Published var progress: [String: ProgressSnapshot] = [:]

    /// Last ETag we saw from the server, for cheap 304 round-trips.
    /// Persisted in UserDefaults so it survives relaunches — without this,
    /// every relaunch is a 200 with full body.
    @Published private(set) var lastETag: String = UserDefaults.standard.string(forKey: "pocket.lastETag") ?? ""

    /// Last successful sync time. Used by the foreground-auto-sync throttle
    /// so we don't fire a sync every time iOS hands us a scenePhase change.
    private var lastSyncAttempt: Date = .distantPast

    /// Throttle window for auto-sync. 30s is short enough to feel live
    /// (open the app on the train, see fresh data) but long enough that
    /// rapid scenePhase bounces don't hammer the server.
    private static let autoSyncThrottle: TimeInterval = 30

    /// Load the initial snapshot. Reads from disk first so the UI shows
    /// data immediately (no spinner on cold start), then triggers a network
    /// sync in the background to refresh.
    func loadInitial() async {
        if AppConfig.useSampleData {
            snapshot = Self.loadSampleSnapshot() ?? .empty
            return
        }
        // 1. Hydrate from disk cache (instant — no spinner)
        if let cached = Self.loadCachedSnapshot() {
            snapshot = cached.snapshot
            lastETag = cached.etag
        }
        // 2. Refresh from network in the background
        await sync()
    }

    /// Force a sync from the server. Uses the last `sync_token` + ETag
    /// for incremental sync with "nothing changed" 304 fast path.
    func sync() async {
        isSyncing = true
        lastError = nil
        defer { isSyncing = false }
        do {
            let since = snapshot.syncToken.isEmpty ? nil : snapshot.syncToken
            let result = try await APIClient.shared.fetchSnapshot(
                since: since,
                ifNoneMatch: lastETag.isEmpty ? nil : lastETag
            )
            if result.notModified {
                // 304 — server says nothing changed. Keep existing snapshot.
                lastSyncDate = Date()
            } else {
                snapshot = merge(snapshot, with: result.snapshot)
                lastETag = result.etag
                UserDefaults.standard.set(result.etag, forKey: Self.lastETagKey)
                Self.persistSnapshot(snapshot, etag: result.etag)
                lastSyncDate = Date()
            }
        } catch {
            lastError = error.localizedDescription
            // If we have nothing, try to fall back to sample data
            if snapshot.courses.isEmpty {
                snapshot = Self.loadSampleSnapshot() ?? .empty
            }
        }
    }

    /// Throttled sync used by the foreground hook. Returns immediately if
    /// we synced less than `autoSyncThrottle` seconds ago.
    func syncIfStale() async {
        let now = Date()
        if now.timeIntervalSince(lastSyncAttempt) < Self.autoSyncThrottle {
            return
        }
        lastSyncAttempt = now
        await sync()
    }

    // MARK: - Disk persistence

    private struct CachedSnapshot: Codable {
        let snapshot: Snapshot
        let etag: String
    }

    private static func persistSnapshot(_ snap: Snapshot, etag: String) {
        do {
            let cached = CachedSnapshot(snapshot: snap, etag: etag)
            let data = try JSONEncoder().encode(cached)
            try data.write(to: snapshotCacheURL, options: .atomic)
        } catch {
            // best-effort: a persist failure is not fatal, the in-memory
            // snapshot is still correct
        }
    }

    private static func loadCachedSnapshot() -> CachedSnapshot? {
        guard let data = try? Data(contentsOf: snapshotCacheURL) else { return nil }
        return try? JSONDecoder().decode(CachedSnapshot.self, from: data)
    }

    /// Per-video: load progress (which chunks are done).
    func loadProgress(videoId: String) async {
        if AppConfig.useSampleData { return }
        do {
            let p = try await APIClient.shared.fetchProgress(videoId: videoId)
            progress[videoId] = p
        } catch {
            // best-effort; non-fatal
        }
    }

    /// Per-video: load cached chunks (no Ollama call).
    func loadCachedChunks(videoId: String) async -> [Chunk] {
        if AppConfig.useSampleData { return [] }
        do {
            return try await APIClient.shared.cachedChunks(videoId: videoId)
        } catch {
            return []
        }
    }

    // MARK: - Helpers

    private func merge(_ old: Snapshot, with new: Snapshot) -> Snapshot {
        // Simple union-by-id. Deleted IDs drop them from the local cache.
        let deleted = Set(new.deletedIds)
        let oldCourses = old.courses.filter { !deleted.contains($0.id) }
        let oldSections = old.sections.filter { !deleted.contains($0.id) }
        let oldVideos = old.videos.filter { !deleted.contains($0.id) }

        let courseMap = Dictionary(uniqueKeysWithValues: oldCourses.map { ($0.id, $0) })
            .merging(Dictionary(uniqueKeysWithValues: new.courses.map { ($0.id, $0) })) { _, n in n }
        let sectionMap = Dictionary(uniqueKeysWithValues: oldSections.map { ($0.id, $0) })
            .merging(Dictionary(uniqueKeysWithValues: new.sections.map { ($0.id, $0) })) { _, n in n }
        let videoMap = Dictionary(uniqueKeysWithValues: oldVideos.map { ($0.id, $0) })
            .merging(Dictionary(uniqueKeysWithValues: new.videos.map { ($0.id, $0) })) { _, n in n }

        return Snapshot(
            courses: Array(courseMap.values).sorted { $0.title < $1.title },
            sections: Array(sectionMap.values).sorted { $0.orderIndex < $1.orderIndex },
            videos: Array(videoMap.values).sorted { $0.orderIndex < $1.orderIndex },
            deletedIds: new.deletedIds,
            syncToken: new.syncToken
        )
    }

    static func loadSampleSnapshot() -> Snapshot? {
        guard let url = Bundle.main.url(forResource: "sample_snapshot", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        return try? dec.decode(Snapshot.self, from: data)
    }

    // MARK: - Convenience accessors

    func sections(for courseId: String) -> [CourseSection] {
        snapshot.sections.filter { $0.courseId == courseId }
            .sorted { $0.orderIndex < $1.orderIndex }
    }

    func videos(for sectionId: String) -> [Video] {
        snapshot.videos.filter { $0.sectionId == sectionId }
            .sorted { $0.orderIndex < $1.orderIndex }
    }

    func video(id: String) -> Video? {
        snapshot.videos.first(where: { $0.id == id })
    }
}
