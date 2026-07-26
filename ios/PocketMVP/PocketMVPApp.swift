import SwiftUI

@main
struct PocketMVPApp: App {
    @StateObject private var store = SnapshotStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
        }
    }
}
