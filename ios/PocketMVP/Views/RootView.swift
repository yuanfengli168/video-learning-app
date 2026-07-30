import SwiftUI

/// Top-level navigation. iOS pattern: NavigationStack with course list as root.
///
/// v0.1.3-real-teaching v0.2 (Firebase auth on iOS): when no user is
/// signed in, show LoginView instead of the course list. Once signed in,
/// FirebaseAuthService.currentUser is non-nil and we swap to the real
/// app shell.
struct RootView: View {
    @EnvironmentObject var store: SnapshotStore
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var auth = FirebaseAuthService.shared

    var body: some View {
        Group {
            if auth.currentUser == nil {
                NavigationStack {
                    LoginView()
                }
            } else {
                NavigationStack {
                    CourseListView()
                        .navigationTitle("Pocket")
                        .toolbar {
                            ToolbarItem(placement: .topBarTrailing) {
                                HStack(spacing: 8) {
                                    SyncStatusDot()
                                    Menu {
                                        Button("Sign out", role: .destructive) {
                                            auth.signOut()
                                        }
                                    } label: {
                                        Image(systemName: "person.crop.circle")
                                    }
                                }
                            }
                        }
                }
            }
        }
        .task {
            auth.configureIfNeeded()
            await store.loadInitial()
        }
        .onChange(of: scenePhase) { _, newPhase in
            // Foreground auto-sync: when the user comes back to the app
            // (from home screen, from another app, after a call), refresh
            // data if the throttle window has passed. Throttled inside
            // `syncIfStale()` so rapid scenePhase bounces don't fire.
            //
            // Skip the sync if there's no signed-in user yet — without
            // a Firebase Bearer token, APIClient falls back to the
            // X-Dev-User-Id header (AppConfig.devUserId), which the
            // prod-mode backend rejects with 401. That 401 surfaces as
            // a "Sync error" alert on the LoginView, which is wrong:
            // the user hasn't even signed in yet. The sync should
            // only run after a successful sign-in.
            if newPhase == .active && auth.currentUser != nil {
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
