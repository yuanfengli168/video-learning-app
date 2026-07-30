import SwiftUI

// MARK: - Per-chunk local state (typed answer, AI feedback, favorite)
//
// Kept inside TeachMeView (not on Chunk) because each student has their own
// answer + AI feedback per chunk — it's runtime UI state, not server data.

final class ChunkLocalState: ObservableObject, Identifiable {
    let id: String           // == chunk.id
    @Published var answer: String = ""
    @Published var feedback: FeedbackResponse?
    @Published var feedbackLoading: Bool = false
    @Published var feedbackError: String?
    @Published var isFavorite: Bool = false
    @Published var isMarkingDone: Bool = false

    init(id: String) {
        self.id = id
    }

    /// Convenience initializer for backward-compatible call sites that
    /// only have a chunk id available as a default.
    convenience init() {
        self.init(id: UUID().uuidString)
    }
}

struct TeachMeView: View {
    let video: Video
    @EnvironmentObject var store: SnapshotStore

    @State private var chunks: [Chunk] = []
    @State private var isLoading = false
    @State private var errorMessage: String? = nil
    @State private var jobStatus: TeachStatus = .pending
    @State private var currentJobId: String? = nil
    @State private var pollCount: Int = 0
    @State private var completedChunks: Set<Int> = []
    @State private var chunkStates: [String: ChunkLocalState] = [:]
    @State private var showFavoritesOnly: Bool = false
    // Ollama availability: nil = not yet checked, true = available, false = offline.
    @State private var tutorAvailable: Bool? = nil
    @State private var tutorDetail: String = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Tutor offline banner — shown only after we've actually checked.
                if tutorAvailable == false {
                    OllamaStatusBanner(
                        detail: tutorDetail,
                        onRetry: { Task { await checkTutorStatus() } }
                    )
                    .padding(.top, 4)
                }

                header

                // MVP0.2: Materials panel (read-only mirror of Mac-side selection)
                MaterialsPanel(
                    videoId: video.id,
                    initialSelectedIds: video.selectedMaterials,
                )

                if isLoading {
                    loadingView
                } else if let err = errorMessage {
                    errorView(err)
                } else if chunks.isEmpty {
                    startView
                } else {
                    chunksList
                }
            }
            .padding()
        }
        .navigationTitle("Teach me")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if !chunks.isEmpty {
                    Button {
                        showFavoritesOnly.toggle()
                    } label: {
                        Image(systemName: showFavoritesOnly ? "heart.fill" : "heart")
                            .foregroundStyle(showFavoritesOnly ? .pink : .secondary)
                    }
                    .accessibilityLabel(showFavoritesOnly ? "Showing favorites only" : "Show favorites only")
                }
            }
        }
        .task {
            // First try cached chunks so reopening the screen is instant
            if !AppConfig.useSampleData {
                chunks = await store.loadCachedChunks(videoId: video.id)
            }
            await store.loadProgress(videoId: video.id)
            if let p = store.progress[video.id] {
                completedChunks = Set(p.chunksDone)
            }
            // Hydrate chunkStates from the per-video detail (answers + favorites + verdicts)
            await loadLocalStateFromServer()
            // Check tutor availability for the offline banner
            if !AppConfig.useSampleData {
                await checkTutorStatus()
            }
        }
    }

    // MARK: - Subviews

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(video.title)
                .font(.headline)
            Text("The AI tutor will break this video into chunks that fit your commute — 2 min, 5 min, or 25 min.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var startView: some View {
        VStack(spacing: 16) {
            Image(systemName: "sparkles")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Button {
                Task { await startTeach() }
            } label: {
                Label("Start teaching", systemImage: "play.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.accentColor)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding(.vertical, 32)
    }

    private var loadingView: some View {
        VStack(spacing: 12) {
            ProgressView()
                .scaleEffect(1.5)
                .padding()
            Text("Tutor is preparing your lesson…")
                .font(.subheadline)
            Text("This usually takes 10–30 seconds.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }

    private func errorView(_ msg: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 36))
                .foregroundStyle(.orange)
            Text(msg)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Try again") { Task { await startTeach() } }
                .padding(.top, 4)
        }
        .padding(.vertical, 24)
    }

    private var visibleChunks: [Chunk] {
        if !showFavoritesOnly { return chunks }
        return chunks.filter { chunkStates[$0.id]?.isFavorite == true }
    }

    private var chunksList: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Your chunks")
                    .font(.headline)
                Spacer()
                if showFavoritesOnly {
                    Text("Showing \(visibleChunks.count) favorite\(visibleChunks.count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if visibleChunks.isEmpty && showFavoritesOnly {
                Text("No favorites yet — tap the heart on any chunk to bookmark it.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 24)
            } else {
                ForEach(visibleChunks) { chunk in
                    ChunkCard(
                        chunk: chunk,
                        state: state(for: chunk),
                        isDone: completedChunks.contains(chunk.index),
                        isLastSeen: store.progress[video.id]?.lastSeenChunk == chunk.index,
                        tutorAvailable: tutorAvailable,
                        onAskFeedback: { Task { await askFeedback(chunk) } },
                        onMarkDone: { Task { await markDone(chunk) } },
                        onToggleFavorite: { Task { await toggleFavorite(chunk) } }
                    )
                }
            }
        }
    }

    // MARK: - Local state helpers

    private func state(for chunk: Chunk) -> ChunkLocalState {
        if let s = chunkStates[chunk.id] { return s }
        let s = ChunkLocalState()
        chunkStates[chunk.id] = s
        return s
    }

    private func loadLocalStateFromServer() async {
        guard !AppConfig.useSampleData else { return }
        do {
            let detail = try await APIClient.shared.fetchProgressDetail(videoId: video.id)
            for item in detail.items {
                let stub = Chunk(
                    id: item.chunkId, videoId: video.id, index: item.chunkIndex,
                    startTs: 0, endTs: 0, durationLabel: .min5,
                    conceptTitle: item.conceptTitle,
                    transcriptQuote: "", teachText: "", checkQuestion: ""
                )
                let s = state(for: stub)
                s.answer = item.userAnswer
                s.isFavorite = item.isFavorite
                if let v = item.verdictEnum, !item.lastAIExplanation.isEmpty {
                    s.feedback = FeedbackResponse(
                        chunkId: item.chunkId,
                        verdict: v,
                        explanation: item.lastAIExplanation
                    )
                }
            }
        } catch {
            // Non-fatal — UI still works, just without hydrated answers.
        }
    }

    // MARK: - Actions

    private func startTeach() async {
        guard !AppConfig.useSampleData else { loadFakeChunks(); return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let job = try await APIClient.shared.startTeach(videoId: video.id)
            currentJobId = job.jobId
            await pollUntilDone()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func pollUntilDone() async {
        guard let jobId = currentJobId else { return }
        isLoading = true
        defer { isLoading = false }
        let startedAt = Date()
        for _ in 0..<AppConfig.teachStatusMaxPolls {
            do {
                let resp = try await APIClient.shared.teachStatus(videoId: video.id, jobId: jobId)
                jobStatus = resp.status
                if resp.status == .ready, let cs = resp.chunks {
                    chunks = cs
                    return
                }
                if resp.status == .error {
                    errorMessage = resp.error ?? "Tutor failed"
                    return
                }
            } catch {
                errorMessage = error.localizedDescription
                return
            }
            try? await Task.sleep(nanoseconds: UInt64(AppConfig.teachStatusPollInterval * 1_000_000_000))
            pollCount += 1
        }
        // Polling exhausted (default 60 min for a 2-hour video). The
        // backend job may still be running — the user can wait, or
        // navigate away and come back later (the job persists server-
        // side and the chunks are cached on completion).
        let elapsed = Int(Date().timeIntervalSince(startedAt) / 60)
        errorMessage = "Tutor is still working after \(elapsed) min. You can wait, or come back later — the lesson will be ready when you return."
    }

    private func markDone(_ chunk: Chunk) async {
        let s = state(for: chunk)
        if AppConfig.useSampleData {
            completedChunks.insert(chunk.index)
            s.isFavorite = !s.isFavorite
            return
        }
        s.isMarkingDone = true
        defer { s.isMarkingDone = false }
        do {
            _ = try await APIClient.shared.markChunkDoneWithAnswer(
                chunkId: chunk.id,
                userAnswer: s.answer,
                isFavorite: s.isFavorite
            )
            completedChunks.insert(chunk.index)
            await store.loadProgress(videoId: video.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func askFeedback(_ chunk: Chunk) async {
        let s = state(for: chunk)
        guard !s.answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            s.feedbackError = "Type your answer first, then ask for feedback."
            return
        }
        // Don't even try when we know Ollama is offline — fail fast with
        // the banner's message so the student knows why nothing happened.
        if tutorAvailable == false {
            s.feedback = FeedbackResponse(
                chunkId: chunk.id,
                verdict: .missed,
                explanation: "AI tutor is offline. \(tutorDetail). Try again in a moment."
            )
            return
        }
        s.feedbackLoading = true
        s.feedbackError = nil
        defer { s.feedbackLoading = false }
        do {
            let resp = try await APIClient.shared.gradeAnswer(
                chunkId: chunk.id,
                userAnswer: s.answer
            )
            s.feedback = resp
            // If the backend reported ollama_unavailable, refresh status.
            if resp.ollamaUnavailable == true {
                tutorAvailable = false
                tutorDetail = "Backend reported tutor offline"
            }
        } catch {
            s.feedbackError = error.localizedDescription
        }
    }

    private func toggleFavorite(_ chunk: Chunk) async {
        let s = state(for: chunk)
        if AppConfig.useSampleData {
            s.isFavorite.toggle()
            return
        }
        do {
            let resp = try await APIClient.shared.toggleFavorite(chunkId: chunk.id)
            s.isFavorite = resp.isFavorite
        } catch {
            s.feedbackError = error.localizedDescription
        }
    }

    // MARK: - Sample data (offline UI dev)

    private func checkTutorStatus() async {
        /// Ping /api/health to learn whether Ollama is reachable. Used to
        /// drive the "AI tutor offline" banner and disable the feedback
        /// button. A nil response means the entire backend is unreachable
        /// (network down, server not running) — we treat that the same
        /// as Ollama being offline so the UI stays consistent.
        if AppConfig.useSampleData {
            tutorAvailable = true
            tutorDetail = "sample data mode"
            return
        }
        let health = await APIClient.shared.fetchHealth()
        if let h = health {
            tutorAvailable = h.ollama.available
            tutorDetail = h.ollama.detail
        } else {
            tutorAvailable = false
            tutorDetail = "Backend unreachable"
        }
    }

    // MARK: - Sample data (offline UI dev)

    private func loadFakeChunks() {
        chunks = [
            Chunk(id: "c1", videoId: video.id, index: 0, startTs: 0, endTs: 120,
                  durationLabel: .min2, conceptTitle: "Hook",
                  transcriptQuote: "The most important idea here is X, because Y.",
                  teachText: "Quick intro. The 30-second version of the whole idea.",
                  checkQuestion: "What's the one thing you'd tell a friend about this?"),
            Chunk(id: "c2", videoId: video.id, index: 1, startTs: 120, endTs: 420,
                  durationLabel: .min5, conceptTitle: "Core concept",
                  transcriptQuote: "We can break this into 3 parts: A, B, and C.",
                  teachText: "The main idea, broken into 3 parts with examples.",
                  checkQuestion: "Name the 3 parts."),
            Chunk(id: "c3", videoId: video.id, index: 2, startTs: 420, endTs: 1920,
                  durationLabel: .min25, conceptTitle: "Deep dive",
                  transcriptQuote: "In production, you'd want to handle the edge case Z.",
                  teachText: "Everything you need to actually use this. The textbook version.",
                  checkQuestion: "How would you apply this to a project at work?"),
        ]
    }
}

// MARK: - Chunk card

struct ChunkCard: View {
    let chunk: Chunk
    @ObservedObject var state: ChunkLocalState
    let isDone: Bool
    let isLastSeen: Bool
    /// When false (Ollama offline), the "Get AI feedback" button is
    /// disabled so the student doesn't waste a network call. The button
    /// label changes to "Tutor offline" so it's clear why.
    let tutorAvailable: Bool?
    let onAskFeedback: () -> Void
    let onMarkDone: () -> Void
    let onToggleFavorite: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack(alignment: .top) {
                Image(systemName: chunk.durationLabel.icon)
                    .foregroundStyle(.tint)
                Text(chunk.conceptTitle)
                    .font(.headline)
                Spacer()
                Text(chunk.durationLabel.displayName)
                    .font(.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color(.tertiarySystemBackground))
                    .clipShape(Capsule())
            }

            // Transcript quote (real teacher cites the source)
            if !chunk.transcriptQuote.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Label("From the video", systemImage: "quote.opening")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text("“\(chunk.transcriptQuote)”")
                        .font(.subheadline)
                        .italic()
                        .foregroundStyle(.primary)
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.tertiarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            // Teach text
            Text(chunk.teachText)
                .font(.body)

            // Check question
            if !chunk.checkQuestion.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Check yourself", systemImage: "questionmark.bubble")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(chunk.checkQuestion)
                        .font(.subheadline)
                }
                .padding(.top, 2)
            }

            // Student answer
            VStack(alignment: .leading, spacing: 4) {
                Label("Your answer", systemImage: "pencil")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                TextEditor(text: $state.answer)
                    .frame(minHeight: 70)
                    .padding(6)
                    .background(Color(.systemBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            // AI feedback
            if let fb = state.feedback {
                FeedbackBox(verdict: fb.verdict, explanation: fb.explanation)
            }
            if let err = state.feedbackError {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            // Actions
            HStack(spacing: 8) {
                let tutorOffline = (tutorAvailable == false)
                Button(action: onAskFeedback) {
                    HStack(spacing: 4) {
                        if state.feedbackLoading {
                            ProgressView().scaleEffect(0.8)
                        } else if tutorOffline {
                            Image(systemName: "wifi.slash")
                        } else {
                            Image(systemName: "sparkles")
                        }
                        Text(tutorOffline
                             ? "Tutor offline"
                             : (state.feedback == nil ? "Get AI feedback" : "Re-grade"))
                    }
                    .font(.subheadline)
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(tutorOffline ? .gray : .purple)
                .disabled(state.feedbackLoading || tutorOffline)

                Button(action: onMarkDone) {
                    HStack(spacing: 4) {
                        if state.isMarkingDone {
                            ProgressView().scaleEffect(0.8)
                        } else {
                            Image(systemName: isDone ? "checkmark.circle.fill" : "checkmark")
                        }
                        Text(isDone ? "Done" : "Mark done")
                    }
                    .font(.subheadline)
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(isDone ? .green : .accentColor)
                .disabled(state.isMarkingDone)

                // Heart / favorite
                Button(action: onToggleFavorite) {
                    Image(systemName: state.isFavorite ? "heart.fill" : "heart")
                        .foregroundStyle(state.isFavorite ? .pink : .secondary)
                        .frame(width: 44, height: 36)
                }
                .buttonStyle(.bordered)
                .accessibilityLabel(state.isFavorite ? "Unfavorite" : "Favorite")
            }

            // Last seen bookmark
            if isLastSeen && !isDone {
                Label("Last seen", systemImage: "bookmark.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(isLastSeen ? Color.orange : Color.clear, lineWidth: 2)
        )
    }
}

// MARK: - AI feedback box

struct FeedbackBox: View {
    let verdict: AIVerdict
    let explanation: String

    private var color: Color {
        switch verdict {
        case .gotIt:   return .green
        case .partial: return .orange
        case .missed:  return .red
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: verdict.icon)
                    .foregroundStyle(color)
                Text("AI: \(verdict.displayName)")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(color)
            }
            Text(explanation)
                .font(.subheadline)
                .foregroundStyle(.primary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.1))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(color.opacity(0.4), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

#Preview {
    NavigationStack {
        TeachMeView(video: Video(
            id: "v1", sectionId: "s1", title: "Sample", orderIndex: 0,
            summary: "", transcript: "", flashcards: "[]", quiz: "[]", mindmap: "",
            selectedMaterials: [],
            updatedAt: Date()
        ))
        .environmentObject(SnapshotStore())
    }
}