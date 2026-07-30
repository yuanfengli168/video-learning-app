// MVP0.2: Course materials models + read-only API methods.
//
// On iOS, materials are a read-only mirror of the Mac web app's
// authoring surface. The user selects which materials are in scope
// for each video from the Mac; iOS just renders the list and the
// tutor uses them as additional LLM context.
//
// The iOS app:
//   - reads `selectedMaterials` from the Video snapshot (already
//     present in /m/snapshot, see SnapshotModels.swift)
//   - can fetch the full metadata list for that video via
//     /api/videos/{id}/materials
//   - can fetch the extracted text of one material via
//     /api/materials/{id}/text
//
// We never POST/DELETE/PUT materials from iOS — the Mac web app
// is the authoring surface.

import Foundation

// MARK: - /api/videos/{id}/materials response

/// One material in the "available" pool for a video (section-scope +
/// sibling-video-scope). Returned by GET /api/videos/{id}/materials.
struct VideoMaterialItem: Codable, Identifiable, Hashable {
    let materialId: String
    let filename: String
    let sizeBytes: Int
    let charCount: Int?
    let addedAt: Date

    enum CodingKeys: String, CodingKey {
        case materialId = "material_id"
        case filename
        case sizeBytes = "size_bytes"
        case charCount = "char_count"
        case addedAt = "added_at"
    }

    /// Stable Identifiable id for SwiftUI ForEach.
    var id: String { materialId }

    /// Pretty file size ("12 KB", "3.4 MB").
    var sizeLabel: String {
        if sizeBytes < 1024 { return "\(sizeBytes) B" }
        if sizeBytes < 1024 * 1024 { return "\(sizeBytes / 1024) KB" }
        return String(format: "%.1f MB", Double(sizeBytes) / 1024.0 / 1024.0)
    }
}

/// Response from GET /api/videos/{id}/materials.
/// `selectedIds` is the user's Mac-side selection; `available` is the
/// full pool of materials that could be selected.
struct VideoMaterialsResponse: Codable {
    let videoId: String
    let selectedIds: [String]
    let available: [VideoMaterialItem]

    enum CodingKeys: String, CodingKey {
        case videoId = "video_id"
        case selectedIds = "selected_ids"
        case available
    }
}