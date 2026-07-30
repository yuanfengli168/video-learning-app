import Foundation

// MARK: - Sync snapshot

struct Course: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let description: String
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title, description
        case updatedAt = "updated_at"
    }
}

struct CourseSection: Codable, Identifiable, Hashable {
    let id: String
    let courseId: String
    let title: String
    let orderIndex: Int
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title
        case courseId = "course_id"
        case orderIndex = "order_index"
        case updatedAt = "updated_at"
    }
}

struct Video: Codable, Identifiable, Hashable {
    let id: String
    let sectionId: String
    let title: String
    let orderIndex: Int
    let summary: String
    let transcript: String
    let flashcards: String    // JSON string
    let quiz: String          // JSON string
    let mindmap: String
    /// MVP0.2: PocketMaterial IDs the user has selected for this video as LLM
    /// context. Empty list = no materials in context. Read-only on iOS; the
    /// user edits the selection from the Mac web app.
    let selectedMaterials: [String]
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title, summary, transcript, flashcards, quiz, mindmap
        case sectionId = "section_id"
        case orderIndex = "order_index"
        case selectedMaterials = "selected_materials"
        case updatedAt = "updated_at"
    }

    /// MVP0.2: backward-compatible init — older snapshots (pre-MVP0.2)
    /// won't include `selected_materials`, so default to [] when missing.
    /// This avoids a crash when an iOS user upgrades without first syncing.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try c.decode(String.self, forKey: .id)
        self.sectionId = try c.decode(String.self, forKey: .sectionId)
        self.title = try c.decode(String.self, forKey: .title)
        self.orderIndex = try c.decode(Int.self, forKey: .orderIndex)
        self.summary = try c.decode(String.self, forKey: .summary)
        self.transcript = try c.decode(String.self, forKey: .transcript)
        self.flashcards = try c.decode(String.self, forKey: .flashcards)
        self.quiz = try c.decode(String.self, forKey: .quiz)
        self.mindmap = try c.decode(String.self, forKey: .mindmap)
        self.selectedMaterials = try c.decodeIfPresent([String].self, forKey: .selectedMaterials) ?? []
        self.updatedAt = try c.decode(Date.self, forKey: .updatedAt)
    }

    /// Memberwise init — needed because the custom `init(from:)` above
    /// prevents the auto-synthesized memberwise initializer.
    /// Used by previews + any test fixtures that build a Video directly.
    init(id: String, sectionId: String, title: String, orderIndex: Int,
         summary: String, transcript: String, flashcards: String, quiz: String,
         mindmap: String, selectedMaterials: [String], updatedAt: Date) {
        self.id = id
        self.sectionId = sectionId
        self.title = title
        self.orderIndex = orderIndex
        self.summary = summary
        self.transcript = transcript
        self.flashcards = flashcards
        self.quiz = quiz
        self.mindmap = mindmap
        self.selectedMaterials = selectedMaterials
        self.updatedAt = updatedAt
    }

    /// Decode the flashcards JSON string into [term, definition] pairs.
    var decodedFlashcards: [(term: String, definition: String)] {
        guard let data = flashcards.data(using: .utf8),
              let arr = try? JSONDecoder().decode([[String: String]].self, from: data)
        else { return [] }
        return arr.compactMap { d in
            guard let t = d["term"], let def = d["definition"] else { return nil }
            return (t, def)
        }
    }

    /// Decode the quiz JSON string into question/options/answer pairs.
    var decodedQuiz: [QuizQuestion] {
        guard let data = quiz.data(using: .utf8),
              let arr = try? JSONDecoder().decode([QuizQuestion].self, from: data)
        else { return [] }
        return arr
    }
}

struct QuizQuestion: Codable, Hashable {
    let question: String
    let options: [String]
    let answer: String
}

struct Snapshot: Codable {
    let courses: [Course]
    let sections: [CourseSection]
    let videos: [Video]
    let deletedIds: [String]
    let syncToken: String

    enum CodingKeys: String, CodingKey {
        case courses, sections, videos
        case deletedIds = "deleted_ids"
        case syncToken = "sync_token"
    }

    static let empty = Snapshot(courses: [], sections: [], videos: [], deletedIds: [], syncToken: "")
}
