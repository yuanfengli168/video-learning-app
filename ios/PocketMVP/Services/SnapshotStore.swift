import Foundation
import Combine

/// Holds the last-synced snapshot in memory and exposes a `sync()` action
/// that fetches from the backend. The store is the single source of truth
/// for the UI: views read `snapshot` and never call the API directly.
@MainActor
final class SnapshotStore: ObservableObject {
    @Published var snapshot: Snapshot = .empty
    @Published var isSyncing: Bool = false
    @Published var lastError: String? = nil
    @Published var lastSyncDate: Date? = nil

    /// Per-video progress cache (in-memory; lost on relaunch — that's fine
    /// for v0.1, the server is authoritative and we re-fetch on demand).
    @Published var progress: [String: ProgressSnapshot] = [:]

    /// Load the initial snapshot (either from the API or the bundled sample).
    func loadInitial() async {
        if AppConfig.useSampleData {
            snapshot = Self.loadSampleSnapshot() ?? .empty
            return
        }
        await sync()
    }

    /// Force a sync from the server. Uses the last `sync_token` for incremental.
    func sync() async {
        isSyncing = true
        lastError = nil
        defer { isSyncing = false }
        do {
            let new = try await APIClient.shared.fetchSnapshot(since: snapshot.syncToken.isEmpty ? nil : snapshot.syncToken)
            snapshot = merge(snapshot, with: new)
            lastSyncDate = Date()
        } catch {
            lastError = error.localizedDescription
            // If we have nothing, try to fall back to sample data
            if snapshot.courses.isEmpty {
                snapshot = Self.loadSampleSnapshot() ?? .empty
            }
        }
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
