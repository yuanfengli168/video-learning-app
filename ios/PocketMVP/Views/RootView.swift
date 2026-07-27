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
                        SyncStatusDot()
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
