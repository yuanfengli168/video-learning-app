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
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title, summary, transcript, flashcards, quiz, mindmap
        case sectionId = "section_id"
        case orderIndex = "order_index"
        case updatedAt = "updated_at"
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
