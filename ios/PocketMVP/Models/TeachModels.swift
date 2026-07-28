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
    let transcriptQuote: String
    let teachText: String
    let checkQuestion: String

    enum CodingKeys: String, CodingKey {
        case id, index
        case videoId = "video_id"
        case startTs = "start_ts"
        case endTs = "end_ts"
        case durationLabel = "duration_label"
        case conceptTitle = "concept_title"
        case transcriptQuote = "transcript_quote"
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

// MARK: - Real teaching (v0.1.3): typed answers + AI feedback + favorites

/// What the AI grader can say about the student's answer.
/// (Mirrors `app.pocket.tutor.VERDICT_*`.)
enum AIVerdict: String, Codable {
    case gotIt = "got_it"
    case partial
    case missed

    var displayName: String {
        switch self {
        case .gotIt:   return "Got it"
        case .partial: return "Partially"
        case .missed:  return "Missed"
        }
    }

    var icon: String {
        switch self {
        case .gotIt:   return "checkmark.circle.fill"
        case .partial: return "minus.circle.fill"
        case .missed:  return "xmark.circle.fill"
        }
    }
}

/// Request body for POST /m/chunk/{id}/feedback — grade a single answer.
struct FeedbackRequest: Codable {
    let userAnswer: String

    enum CodingKeys: String, CodingKey {
        case userAnswer = "user_answer"
    }
}

/// Response body for POST /m/chunk/{id}/feedback.
struct FeedbackResponse: Codable {
    let chunkId: String
    let verdict: AIVerdict
    let explanation: String
    /// True when the AI tutor (Ollama) is offline. When true, the
    /// explanation is a friendly fallback message instead of an AI
    /// verdict. The iOS UI uses this to refresh the "tutor offline"
    /// banner and disable the feedback button until Ollama is back.
    let ollamaUnavailable: Bool?

    enum CodingKeys: String, CodingKey {
        case chunkId = "chunk_id"
        case verdict, explanation
        case ollamaUnavailable = "ollama_unavailable"
    }

    init(chunkId: String, verdict: AIVerdict, explanation: String, ollamaUnavailable: Bool? = nil) {
        self.chunkId = chunkId
        self.verdict = verdict
        self.explanation = explanation
        self.ollamaUnavailable = ollamaUnavailable
    }
}

/// Request body for POST /m/chunk/{id}/done — combined "mark done + save answer + (optionally) favorite".
struct MarkDoneWithAnswerRequest: Codable {
    let userAnswer: String
    let isFavorite: Bool?

    enum CodingKeys: String, CodingKey {
        case userAnswer = "user_answer"
        case isFavorite = "is_favorite"
    }
}

/// Response body for POST /m/chunk/{id}/favorite.
struct FavoriteToggleResponse: Codable {
    let chunkId: String
    let isFavorite: Bool

    enum CodingKeys: String, CodingKey {
        case chunkId = "chunk_id"
        case isFavorite = "is_favorite"
    }
}

/// One progress row used in the per-video progress detail view.
/// Captures what the student typed + the last AI verdict so the iOS
/// app can render "your answers" without re-querying Ollama.
struct ProgressDetailItem: Codable, Identifiable, Hashable {
    let chunkId: String
    let chunkIndex: Int
    let conceptTitle: String
    let isDone: Bool
    let userAnswer: String
    let isFavorite: Bool
    let lastAIVerdict: String
    let lastAIExplanation: String

    var id: String { chunkId }

    enum CodingKeys: String, CodingKey {
        case chunkId = "chunk_id"
        case chunkIndex = "chunk_index"
        case conceptTitle = "concept_title"
        case isDone = "is_done"
        case userAnswer = "user_answer"
        case isFavorite = "is_favorite"
        case lastAIVerdict = "last_ai_verdict"
        case lastAIExplanation = "last_ai_explanation"
    }

    var verdictEnum: AIVerdict? {
        AIVerdict(rawValue: lastAIVerdict)
    }
}

/// Response for GET /m/progress/{video_id}/detail.
struct ProgressDetailResponse: Codable {
    let videoId: String
    let items: [ProgressDetailItem]

    enum CodingKeys: String, CodingKey {
        case videoId = "video_id"
        case items
    }
}

/// One favorite chunk in GET /m/favorites/{video_id}.
struct FavoriteChunk: Codable, Identifiable, Hashable {
    let chunkId: String
    let chunkIndex: Int
    let conceptTitle: String
    let transcriptQuote: String
    let userAnswer: String
    let lastAIVerdict: String

    var id: String { chunkId }

    enum CodingKeys: String, CodingKey {
        case chunkId = "chunk_id"
        case chunkIndex = "chunk_index"
        case conceptTitle = "concept_title"
        case transcriptQuote = "transcript_quote"
        case userAnswer = "user_answer"
        case lastAIVerdict = "last_ai_verdict"
    }
}

/// Response for GET /m/favorites/{video_id}.
struct FavoritesResponse: Codable {
    let videoId: String
    let favorites: [FavoriteChunk]

    enum CodingKeys: String, CodingKey {
        case videoId = "video_id"
        case favorites
    }
}
