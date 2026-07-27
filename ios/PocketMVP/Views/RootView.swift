import SwiftUI

/// Top-level navigation. iOS pattern: NavigationStack with course list as root.
struct RootView: View {
    @EnvironmentObject var store: SnapshotStore
    @Environment(\.scenePhase) private var scenePhase

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
        .onChange(of: scenePhase) { _, newPhase in
            // Foreground auto-sync: when the user comes back to the app
            // (from home screen, from another app, after a call), refresh
            // data if the throttle window has passed. Throttled inside
            // `syncIfStale()` so rapid scenePhase bounces don't fire.
            if newPhase == .active {
                Task { await store.syncIfStale() }
            }
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
