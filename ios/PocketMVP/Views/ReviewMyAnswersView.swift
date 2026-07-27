import SwiftUI

// MARK: - Review my answers
//
// Shows the student's typed answers + AI verdicts for every chunk in the video.
// Reached from VideoDetailView via "Review my answers". Uses
// GET /m/progress/{video_id}/detail.

struct ReviewMyAnswersView: View {
    let video: Video

    @State private var items: [ProgressDetailItem] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showOnlyWithAnswers: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 32)
                } else if let err = errorMessage {
                    VStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                            .font(.system(size: 36))
                        Text(err)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                        Button("Try again") { Task { await load() } }
                    }
                    .padding(.vertical, 24)
                } else if visibleItems.isEmpty {
                    emptyState
                } else {
                    ForEach(visibleItems) { item in
                        ReviewCard(item: item)
                    }
                }
            }
            .padding()
        }
        .navigationTitle("Review my answers")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showOnlyWithAnswers.toggle()
                } label: {
                    Image(systemName: showOnlyWithAnswers ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                        .foregroundStyle(showOnlyWithAnswers ? Color.accentColor : Color.secondary)
                }
                .accessibilityLabel(showOnlyWithAnswers ? "Showing all" : "Show only answered")
            }
        }
        .task { await load() }
    }

    private var visibleItems: [ProgressDetailItem] {
        if !showOnlyWithAnswers { return items }
        return items.filter { !$0.userAnswer.isEmpty || $0.isDone }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "pencil.slash")
                .font(.system(size: 48))
                .foregroundStyle(.tertiary)
            Text("No answers yet")
                .font(.headline)
            Text("Tap into a chunk, type your answer, and ask the AI tutor for feedback. Your answers and verdicts will show up here.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }

    private func load() async {
        guard !AppConfig.useSampleData else {
            // Sample fallback — show nothing real, but keep the UI alive
            isLoading = false
            return
        }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let detail = try await APIClient.shared.fetchProgressDetail(videoId: video.id)
            items = detail.items
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct ReviewCard: View {
    let item: ProgressDetailItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(item.conceptTitle)
                    .font(.headline)
                Spacer()
                if item.isFavorite {
                    Image(systemName: "heart.fill")
                        .foregroundStyle(.pink)
                        .font(.caption)
                }
                if item.isDone {
                    Label("Done", systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
            }

            if !item.userAnswer.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Your answer")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(item.userAnswer)
                        .font(.body)
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.tertiarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                Text("No answer typed yet.")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }

            if let verdict = item.verdictEnum {
                FeedbackBox(verdict: verdict, explanation: item.lastAIExplanation)
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}