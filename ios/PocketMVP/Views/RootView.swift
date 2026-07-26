import SwiftUI

/// Top-level navigation. iOS pattern: NavigationStack with course list as root.
struct RootView: View {
    @EnvironmentObject var store: SnapshotStore

    var body: some View {
        NavigationStack {
            CourseListView()
                .navigationTitle("Pocket")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            Task { await store.sync() }
                        } label: {
                            if store.isSyncing {
                                ProgressView()
                            } else {
                                Image(systemName: "arrow.clockwise")
                            }
                        }
                        .accessibilityLabel("Sync from server")
                    }
                }
        }
        .task {
            await store.loadInitial()
        }
        .alert("Sync error", isPresented: .constant(store.lastError != nil), actions: {
            Button("OK") { store.lastError = nil }
        }, message: {
            Text(store.lastError ?? "")
        })
    }
}

#Preview {
    RootView()
        .environmentObject(SnapshotStore())
}
