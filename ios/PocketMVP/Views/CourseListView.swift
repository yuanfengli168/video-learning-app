import SwiftUI

struct CourseListView: View {
    @EnvironmentObject var store: SnapshotStore
    @State private var showSettings = false

    var body: some View {
        List {
            if store.snapshot.courses.isEmpty {
                emptyState
            } else {
                ForEach(store.snapshot.courses) { course in
                    NavigationLink(value: course) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(course.title)
                                .font(.headline)
                            if !course.description.isEmpty {
                                Text(course.description)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                            let sectionCount = store.sections(for: course.id).count
                            let videoCount = store.snapshot.videos.filter { v in
                                store.sections(for: course.id).contains(where: { $0.id == v.sectionId })
                            }.count
                            Text("\(sectionCount) section\(sectionCount == 1 ? "" : "s") · \(videoCount) video\(videoCount == 1 ? "" : "s")")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationDestination(for: Course.self) { course in
            SectionListView(course: course)
        }
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    showSettings = true
                } label: {
                    Image(systemName: "gear")
                }
                .accessibilityLabel("Settings")
            }
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .refreshable {
            await store.sync()
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "books.vertical")
                .font(.system(size: 48))
                .foregroundStyle(.tertiary)
            Text("No courses yet")
                .font(.headline)
            Text("Pull down to sync from your Mac.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 40)
        .listRowSeparator(.hidden)
    }
}

#Preview {
    NavigationStack {
        CourseListView()
            .environmentObject(SnapshotStore())
    }
}
