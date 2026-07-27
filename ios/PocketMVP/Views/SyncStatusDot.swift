import SwiftUI

/// Small status indicator that reflects the snapshot store's sync state.
///
/// States:
/// - `.gray` (default) — never synced yet, or sample-data mode
/// - `.green` — last sync succeeded, age < 60s
/// - `.blue` pulsing — sync in flight right now
/// - `.red` — last sync failed; tap to retry
struct SyncStatusDot: View {
    @EnvironmentObject var store: SnapshotStore

    var body: some View {
        Button {
            Task { await store.sync() }
        } label: {
            HStack(spacing: 6) {
                dot
                if let last = store.lastSyncDate {
                    Text(relativeTime(last))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 4)
        }
        .accessibilityLabel(accessibilityText)
    }

    // MARK: - The dot itself

    @ViewBuilder
    private var dot: some View {
        Circle()
            .fill(color)
            .frame(width: 10, height: 10)
            .overlay {
                if store.isSyncing {
                    Circle()
                        .stroke(color, lineWidth: 2)
                        .scaleEffect(1.8)
                        .opacity(0.4)
                        .animation(
                            .easeOut(duration: 1.2).repeatForever(autoreverses: false),
                            value: store.isSyncing
                        )
                }
            }
    }

    private var color: Color {
        if store.isSyncing { return .blue }
        if store.lastError != nil { return .red }
        if let last = store.lastSyncDate {
            return Date().timeIntervalSince(last) < 60 ? .green : .orange
        }
        return .gray
    }

    private var accessibilityText: String {
        if store.isSyncing { return "Syncing now" }
        if store.lastError != nil { return "Sync failed, tap to retry" }
        if let last = store.lastSyncDate {
            return "Last synced \(relativeTime(last))"
        }
        return "Not yet synced"
    }

    // MARK: - Helpers

    private func relativeTime(_ date: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: date, relativeTo: Date())
    }
}

#Preview {
    SyncStatusDot()
        .environmentObject(SnapshotStore())
}
