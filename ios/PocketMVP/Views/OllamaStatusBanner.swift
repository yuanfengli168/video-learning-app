import SwiftUI

// MARK: - Ollama offline banner
//
// Shown at the top of TeachMeView when the AI tutor (Ollama) is
// unreachable. Tells the student:
//   1. The tutor is offline (so the "Get AI feedback" button is disabled)
//   2. Their typed answer is still saved (no data loss)
//   3. They can retry by tapping the refresh button
//
// Color-coded so it's clearly an error state (not a normal status).

struct OllamaStatusBanner: View {
    let detail: String           // e.g. "unreachable: Connection refused"
    let onRetry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.white)
                .font(.title3)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                Text("AI tutor offline")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text("Your answers are saved. Make sure Ollama is running on your Mac, then tap retry.")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            Button(action: onRetry) {
                Image(systemName: "arrow.clockwise")
                    .foregroundStyle(.white)
                    .padding(8)
                    .background(Color.white.opacity(0.18))
                    .clipShape(Circle())
            }
            .accessibilityLabel("Retry checking tutor status")
        }
        .padding(12)
        .background(Color.orange)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .padding(.horizontal)
    }
}

#Preview {
    VStack(spacing: 12) {
        OllamaStatusBanner(
            detail: "unreachable: Connection refused",
            onRetry: { }
        )
        Spacer()
    }
    .padding(.vertical)
}