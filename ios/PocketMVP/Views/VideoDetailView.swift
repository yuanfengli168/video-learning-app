import SwiftUI

struct VideoDetailView: View {
    let video: Video
    @State private var selectedTab: Tab = .summary

    enum Tab: String, CaseIterable, Hashable {
        case summary = "Summary"
        case quiz = "Quiz"
        case flashcards = "Flashcards"
        case mindmap = "Mindmap"

        var icon: String {
            switch self {
            case .summary:    return "text.alignleft"
            case .quiz:       return "questionmark.circle"
            case .flashcards: return "rectangle.stack"
            case .mindmap:    return "brain.head.profile"
            }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Tab bar
            Picker("", selection: $selectedTab) {
                ForEach(Tab.allCases, id: \.self) { tab in
                    Label(tab.rawValue, systemImage: tab.icon)
                        .tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .padding()

            Divider()

            // Content
            ScrollView {
                Group {
                    switch selectedTab {
                    case .summary:    SummaryPane(text: video.summary)
                    case .quiz:       QuizPane(questions: video.decodedQuiz)
                    case .flashcards: FlashcardsPane(cards: video.decodedFlashcards)
                    case .mindmap:    MindmapPane(markdown: video.mindmap)
                    }
                }
                .padding()
            }

            Divider()

            // Bottom: "Teach me" button (with optional materials badge — MVP0.2)
            NavigationLink(destination: TeachMeView(video: video)) {
                HStack(spacing: 8) {
                    Label("Teach me", systemImage: "graduationcap.fill")
                        .font(.headline)
                    if !video.selectedMaterials.isEmpty {
                        // Pill: "📄 N" so the user knows materials are in scope
                        // without having to open the TeachMe view.
                        HStack(spacing: 2) {
                            Image(systemName: "doc.text.fill").font(.caption)
                            Text("\(video.selectedMaterials.count)")
                                .font(.caption).bold()
                        }
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.white.opacity(0.25))
                        .clipShape(Capsule())
                    }
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(Color.accentColor)
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .padding()

            // Secondary actions: review answers + see favorites
            HStack(spacing: 8) {
                NavigationLink(destination: ReviewMyAnswersView(video: video)) {
                    Label("Review answers", systemImage: "list.bullet.rectangle")
                        .font(.subheadline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Color(.tertiarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                NavigationLink(destination: FavoritesView(video: video)) {
                    Label("Favorites", systemImage: "heart")
                        .font(.subheadline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Color(.tertiarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(.horizontal)
            .padding(.bottom, 12)
        }
        .navigationTitle(video.title)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            // Eagerly load progress for this video
            // (handled in store.loadProgress)
        }
    }
}

// MARK: - Tab panes

struct SummaryPane: View {
    let text: String
    var body: some View {
        if text.isEmpty {
            EmptyState(icon: "doc.text", message: "No summary generated yet.")
        } else {
            Text(text)
                .font(.body)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct QuizPane: View {
    let questions: [QuizQuestion]
    @State private var revealed: Set<Int> = []

    var body: some View {
        if questions.isEmpty {
            EmptyState(icon: "questionmark.circle", message: "No quiz generated yet.")
        } else {
            VStack(alignment: .leading, spacing: 20) {
                ForEach(Array(questions.enumerated()), id: \.offset) { idx, q in
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Q\(idx + 1). \(q.question)")
                            .font(.headline)
                        ForEach(q.options, id: \.self) { opt in
                            HStack(alignment: .top) {
                                Image(systemName: revealed.contains(idx) && opt == q.answer
                                      ? "checkmark.circle.fill"
                                      : "circle")
                                    .foregroundStyle(revealed.contains(idx) && opt == q.answer ? .green : .secondary)
                                Text(opt)
                                    .font(.subheadline)
                            }
                        }
                        if revealed.contains(idx) {
                            Text("Answer: \(q.answer)")
                                .font(.caption)
                                .foregroundStyle(.green)
                        } else {
                            Button("Show answer") { revealed.insert(idx) }
                                .font(.caption)
                        }
                    }
                    .padding()
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }
        }
    }
}

struct FlashcardsPane: View {
    let cards: [(term: String, definition: String)]
    @State private var flipped: Set<Int> = []

    var body: some View {
        if cards.isEmpty {
            EmptyState(icon: "rectangle.stack", message: "No flashcards generated yet.")
        } else {
            VStack(spacing: 12) {
                ForEach(Array(cards.enumerated()), id: \.offset) { idx, card in
                    Button {
                        if flipped.contains(idx) { flipped.remove(idx) } else { flipped.insert(idx) }
                    } label: {
                        VStack {
                            Text(flipped.contains(idx) ? card.definition : card.term)
                                .font(flipped.contains(idx) ? .body : .title3)
                                .multilineTextAlignment(.center)
                                .padding()
                                .frame(maxWidth: .infinity, minHeight: 100)
                        }
                        .background(Color(.secondarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)
                }
                Text("Tap a card to flip")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

struct MindmapPane: View {
    let markdown: String
    var body: some View {
        if markdown.isEmpty {
            EmptyState(icon: "brain.head.profile", message: "No mindmap generated yet.")
        } else {
            Text(markdown)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct EmptyState: View {
    let icon: String
    let message: String
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 48))
                .foregroundStyle(.tertiary)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 40)
    }
}

#Preview {
    NavigationStack {
        VideoDetailView(video: Video(
            id: "v1", sectionId: "s1", title: "Sample", orderIndex: 0,
            summary: "## Summary\nA test summary.",
            transcript: "...",
            flashcards: "[]",
            quiz: "[]",
            mindmap: "# Mindmap",
            selectedMaterials: [],
            updatedAt: Date()
        ))
    }
}
