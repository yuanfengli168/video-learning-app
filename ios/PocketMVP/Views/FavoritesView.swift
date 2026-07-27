import SwiftUI

// MARK: - Favorites
//
// Shows all favorited chunks for a video. Reached from VideoDetailView.
// Uses GET /m/favorites/{video_id}.

struct FavoritesView: View {
    let video: Video

    @State private var items: [FavoriteChunk] = []
    @State private var isLoading = false
    @State private var errorMessage: String?

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
                } else if items.isEmpty {
                    emptyState
                } else {
                    ForEach(items) { item in
                        FavoriteCard(item: item)
                    }
                }
            }
            .padding()
        }
        .navigationTitle("Favorites")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "heart.slash")
                .font(.system(size: 48))
                .foregroundStyle(.tertiary)
            Text("No favorites yet")
                .font(.headline)
            Text("Tap the heart on any chunk to bookmark the parts you want to come back to.")
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
            isLoading = false
            return
        }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let resp = try await APIClient.shared.fetchFavorites(videoId: video.id)
            items = resp.favorites
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct FavoriteCard: View {
    let item: FavoriteChunk

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(item.conceptTitle)
                    .font(.headline)
                Spacer()
                Image(systemName: "heart.fill")
                    .foregroundStyle(.pink)
            }

            if !item.transcriptQuote.isEmpty {
                Text("“\(item.transcriptQuote)”")
                    .font(.subheadline)
                    .italic()
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(.tertiarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            if !item.userAnswer.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Your answer")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(item.userAnswer)
                        .font(.subheadline)
                }
            }

            if let v = AIVerdict(rawValue: item.lastAIVerdict) {
                FeedbackBox(verdict: v, explanation: "")
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}