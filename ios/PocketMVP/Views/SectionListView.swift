import SwiftUI

struct SectionListView: View {
    let course: Course
    @EnvironmentObject var store: SnapshotStore

    var sections: [CourseSection] {
        store.sections(for: course.id)
    }

    var body: some View {
        List {
            ForEach(sections) { section in
                Section(section.title) {
                    ForEach(store.videos(for: section.id)) { video in
                        NavigationLink(value: video) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(video.title)
                                    .font(.body)
                                let chunksDone = store.progress[video.id]?.chunksDone.count ?? 0
                                if chunksDone > 0 {
                                    Text("\(chunksDone) chunk\(chunksDone == 1 ? "" : "s") learned")
                                        .font(.caption)
                                        .foregroundStyle(.green)
                                }
                            }
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(course.title)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: Video.self) { video in
            VideoDetailView(video: video)
        }
    }
}

#Preview {
    NavigationStack {
        SectionListView(course: Course(id: "c1", title: "ML", description: "", updatedAt: Date()))
            .environmentObject(SnapshotStore())
    }
}
