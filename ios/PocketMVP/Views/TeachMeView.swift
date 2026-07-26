import SwiftUI

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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header

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
        .task {
            // First try cached chunks so reopening the screen is instant
            if !AppConfig.useSampleData {
                chunks = await store.loadCachedChunks(videoId: video.id)
            }
            await store.loadProgress(videoId: video.id)
            if let p = store.progress[video.id] {
                completedChunks = Set(p.chunksDone)
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

    private var chunksList: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your chunks")
                .font(.headline)
            ForEach(chunks) { chunk in
                ChunkCard(
                    chunk: chunk,
                    isDone: completedChunks.contains(chunk.index),
                    isLastSeen: store.progress[video.id]?.lastSeenChunk == chunk.index,
                    onMarkDone: { Task { await markDone(chunk) } }
                )
            }
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
        errorMessage = "Tutor is taking longer than expected. Try again later."
    }

    private func markDone(_ chunk: Chunk) async {
        if AppConfig.useSampleData {
            completedChunks.insert(chunk.index)
            return
        }
        do {
            _ = try await APIClient.shared.markChunkDone(chunkId: chunk.id)
            completedChunks.insert(chunk.index)
            await store.loadProgress(videoId: video.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Sample data (offline UI dev)

    private func loadFakeChunks() {
        chunks = [
            Chunk(id: "c1", videoId: video.id, index: 0, startTs: 0, endTs: 120,
                  durationLabel: .min2, conceptTitle: "Hook",
                  teachText: "Quick intro. The 30-second version of the whole idea.",
                  checkQuestion: "What's the one thing you'd tell a friend about this?"),
            Chunk(id: "c2", videoId: video.id, index: 1, startTs: 120, endTs: 420,
                  durationLabel: .min5, conceptTitle: "Core concept",
                  teachText: "The main idea, broken into 3 parts with examples.",
                  checkQuestion: "Name the 3 parts."),
            Chunk(id: "c3", videoId: video.id, index: 2, startTs: 420, endTs: 1920,
                  durationLabel: .min25, conceptTitle: "Deep dive",
                  teachText: "Everything you need to actually use this. The textbook version.",
                  checkQuestion: "How would you apply this to a project at work?"),
        ]
    }
}

// MARK: - Chunk card

struct ChunkCard: View {
    let chunk: Chunk
    let isDone: Bool
    let isLastSeen: Bool
    let onMarkDone: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
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
                    .background(Color(.secondarySystemBackground))
                    .clipShape(Capsule())
            }

            Text("≈ \(Int((chunk.endTs - chunk.startTs) / 60)) min of source material")
                .font(.caption2)
                .foregroundStyle(.tertiary)

            Text(chunk.teachText)
                .font(.body)

            if !chunk.checkQuestion.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Check yourself", systemImage: "questionmark.bubble")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(chunk.checkQuestion)
                        .font(.subheadline)
                }
                .padding(.top, 4)
            }

            HStack {
                if isDone {
                    Label("Done", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                } else if isLastSeen {
                    Label("Last seen", systemImage: "bookmark.fill")
                        .foregroundStyle(.orange)
                }
                Spacer()
                Button(action: onMarkDone) {
                    Text(isDone ? "Mark again" : "Mark done")
                        .font(.subheadline)
                }
                .buttonStyle(.borderedProminent)
                .tint(isDone ? .green : .accentColor)
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

#Preview {
    NavigationStack {
        TeachMeView(video: Video(
            id: "v1", sectionId: "s1", title: "Sample", orderIndex: 0,
            summary: "", transcript: "", flashcards: "[]", quiz: "[]", mindmap: "",
            updatedAt: Date()
        ))
        .environmentObject(SnapshotStore())
    }
}
