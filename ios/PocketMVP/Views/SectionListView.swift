import SwiftUI

/// Sort direction for the section list. Persisted via @AppStorage so the
/// user's choice survives across view rebuilds and app restarts.
///
/// MVP0.2 followup #5: user feedback was "should have an asc or desc sort
/// by name". MVP0.2 followup #6: combined into a single toolbar menu
/// with "expand all / collapse all" to keep the UI uncluttered.
enum SectionSortOrder: String, CaseIterable, Identifiable {
    case ascending = "asc"
    case descending = "desc"
    var id: String { rawValue }
    var label: String {
        switch self {
        case .ascending:  return "Name A → Z"
        case .descending: return "Name Z → A"
        }
    }
    var systemImage: String {
        switch self {
        case .ascending:  return "arrow.up"
        case .descending: return "arrow.down"
        }
    }
}

struct SectionListView: View {
    let course: Course
    @EnvironmentObject var store: SnapshotStore

    // MVP0.2 followup #6: persisted preferences. @AppStorage handles
    // write-through to UserDefaults so the state survives app restarts.
    // Single global key for collapse — covers all courses. Per-course
    // was considered but adds complexity for marginal benefit (the
    // user only sees one course at a time anyway).

    /// Sort order. One global default for all courses in v0.1.
    @AppStorage("sectionSortOrder.default") private var sortOrderRaw: String = SectionSortOrder.ascending.rawValue

    /// MVP0.2 followup #7: same idea as the section sort, but for the
    /// videos inside each section. Persisted across app restarts.
    @AppStorage("videoSortOrder.default") private var videoSortOrderRaw: String = VideoSortOrder.ascending.rawValue

    /// Global JSON-encoded set of collapsed section IDs. We use a
    /// JSON string (rather than a dict) because @AppStorage doesn't
    /// support collections directly. The string is `["id1","id2"]` etc.
    @AppStorage("sectionCollapsed.v1") private var collapsedJSON: String = "[]"

    private var sortOrder: SectionSortOrder {
        SectionSortOrder(rawValue: sortOrderRaw) ?? .ascending
    }

    private var videoSortOrder: VideoSortOrder {
        VideoSortOrder(rawValue: videoSortOrderRaw) ?? .ascending
    }

    /// Decoded set of collapsed section IDs. Empty when the JSON is
    /// malformed (first-launch, schema change, etc.).
    private var collapsedSet: Set<String> {
        guard let data = collapsedJSON.data(using: .utf8) else { return [] }
        return (try? JSONDecoder().decode(Set<String>.self, from: data)) ?? []
    }

    private func saveCollapsedSet(_ set: Set<String>) {
        guard let data = try? JSONEncoder().encode(set),
              let s = String(data: data, encoding: .utf8) else { return }
        collapsedJSON = s
    }

    /// Sections sorted by title according to the user's chosen order.
    /// We sort case-insensitively so "Apple" and "apple" stay together
    /// (locale-aware collation handles CJK correctly).
    private var sortedSections: [CourseSection] {
        let raw = store.sections(for: course.id)
        switch sortOrder {
        case .ascending:
            return raw.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
        case .descending:
            return raw.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedDescending }
        }
    }

    var body: some View {
        List {
            ForEach(sortedSections) { section in
                let isCollapsed = collapsedSet.contains(section.id)
                let videos = store.videos(for: section.id, naturalSort: videoSortOrder)
                let totalChunksDone = videos.reduce(0) { acc, v in
                    acc + (store.progress[v.id]?.chunksDone.count ?? 0)
                }

                Section {
                    // Title row — always visible, tap to toggle.
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            toggle(section.id)
                        }
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "chevron.down")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                                .rotationEffect(.degrees(isCollapsed ? -90 : 0))
                                .frame(width: 14)
                            Text(section.title)
                                .font(.headline)
                                .foregroundStyle(.primary)
                            Spacer()
                            Text("\(videos.count) video\(videos.count == 1 ? "" : "s")")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if totalChunksDone > 0 {
                                Text("· \(totalChunksDone) done")
                                    .font(.caption2)
                                    .foregroundStyle(.green)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    // Video list — only when expanded.
                    if !isCollapsed {
                        ForEach(videos) { video in
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
        }
        .listStyle(.insetGrouped)
        .navigationTitle(course.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // MVP0.2 followup #6: single toolbar menu combining sort +
            // expand/collapse all. Keeps the top-right clustered in one
            // icon so the nav bar doesn't get crowded as we add features.
            // The menu only shows when there are ≥2 sections (sorting 1
            // item is a no-op, and expand/collapse all is a no-op).
            if store.sections(for: course.id).count >= 2 {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        sortSection
                        Divider()
                        videoSortSection
                        Divider()
                        expandAllButton
                        collapseAllButton
                    } label: {
                        Image(systemName: "arrow.up.arrow.down")
                    }
                    .accessibilityLabel("Sections menu")
                }
            }
        }
        .navigationDestination(for: Video.self) { video in
            VideoDetailView(video: video)
        }
    }

    // MARK: - Toolbar menu pieces

    /// Sort order picker. Picker shows a checkmark next to the current
    /// selection — what users expect from a single-select menu.
    @ViewBuilder
    private var sortSection: some View {
        Picker("Sort sections", selection: $sortOrderRaw) {
            ForEach(SectionSortOrder.allCases) { order in
                Label(order.label, systemImage: order.systemImage)
                    .tag(order.rawValue)
            }
        }
    }

    /// MVP0.2 followup #7: video sort picker. Same shape as the section
    /// sort so the menu reads consistently. Lives in the same toolbar
    /// menu so the top-right stays a single icon.
    @ViewBuilder
    private var videoSortSection: some View {
        Picker("Sort videos", selection: $videoSortOrderRaw) {
            ForEach(VideoSortOrder.allCases) { order in
                Label(order.label, systemImage: order.systemImage)
                    .tag(order.rawValue)
            }
        }
    }

    /// "Expand all" — clears the collapsed set so every section shows
    /// its videos. Handy when the user wants to see the full course
    /// at a glance.
    private var expandAllButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.15)) {
                saveCollapsedSet([])
            }
        } label: {
            Label("Expand all", systemImage: "arrow.down.to.line")
        }
    }

    /// "Collapse all" — adds every section's ID to the collapsed set
    /// so only the section titles are visible. The user wants "easier
    /// to go to next soonest" — collapsed-all makes the section list
    /// a single screen of titles.
    private var collapseAllButton: some View {
        Button {
            let allIds = Set(sortedSections.map { $0.id })
            withAnimation(.easeInOut(duration: 0.15)) {
                saveCollapsedSet(allIds)
            }
        } label: {
            Label("Collapse all", systemImage: "arrow.up.to.line")
        }
    }

    // MARK: - Helpers

    /// Toggle a single section's collapse state. Persists the new set
    /// to @AppStorage so the change survives a relaunch.
    private func toggle(_ sectionId: String) {
        var set = collapsedSet
        if set.contains(sectionId) {
            set.remove(sectionId)
        } else {
            set.insert(sectionId)
        }
        saveCollapsedSet(set)
    }
}

#Preview {
    NavigationStack {
        SectionListView(course: Course(id: "c1", title: "ML", description: "", updatedAt: Date()))
            .environmentObject(SnapshotStore())
    }
}