import Foundation

// MARK: - Tutor chunks

enum DurationLabel: String, Codable, CaseIterable {
    case min2 = "2min"
    case min5 = "5min"
    case min25 = "25min"

    var displayName: String {
        switch self {
        case .min2:  return "2 min"
        case .min5:  return "5 min"
        case .min25: return "25 min"
        }
    }

    var icon: String {
        switch self {
        case .min2:  return "bolt.fill"
        case .min5:  return "timer"
        case .min25: return "book.fill"
        }
    }
}

struct Chunk: Codable, Identifiable, Hashable {
    let id: String
    let videoId: String
    let index: Int
    let startTs: Double
    let endTs: Double
    let durationLabel: DurationLabel
    let conceptTitle: String
    let teachText: String
    let checkQuestion: String

    enum CodingKeys: String, CodingKey {
        case id, index
        case videoId = "video_id"
        case startTs = "start_ts"
        case endTs = "end_ts"
        case durationLabel = "duration_label"
        case conceptTitle = "concept_title"
        case teachText = "teach_text"
        case checkQuestion = "check_question"
    }
}

// MARK: - Job lifecycle

struct TeachJobCreated: Codable {
    let jobId: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case status
    }
}

enum TeachStatus: String, Codable {
    case pending
    case ready
    case error
}

struct TeachStatusResponse: Codable {
    let jobId: String
    let status: TeachStatus
    let chunks: [Chunk]?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case status, chunks, error
    }
}

// MARK: - Progress

struct ChunkDone: Codable {
    let chunkId: String
    let videoId: String
    let completed: Bool

    enum CodingKeys: String, CodingKey {
        case chunkId = "chunk_id"
        case videoId = "video_id"
        case completed
    }
}

struct ProgressSnapshot: Codable {
    let videoId: String
    let chunksDone: [Int]
    let lastSeenChunk: Int?
    let lastSeenAt: Date?

    enum CodingKeys: String, CodingKey {
        case videoId = "video_id"
        case chunksDone = "chunks_done"
        case lastSeenChunk = "last_seen_chunk"
        case lastSeenAt = "last_seen_at"
    }
}
