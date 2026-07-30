// MVP0.2: Materials panel for TeachMeView.
//
// Read-only mirror of the Mac web app's per-video materials selection.
// Shows:
//   - the count of selected materials + total chars in context
//   - a list with filename + size + char count
//   - a tap target that opens a viewer sheet with the extracted text
//
// No upload / edit UI on iOS — that's Mac-only.
//
// This view is intentionally lightweight: it doesn't trigger a tutor
// job, doesn't make decisions about which materials to use (the Mac
// already did), and doesn't cache. It just renders the data the user
// set up on their Mac.

import SwiftUI

struct MaterialsPanel: View {
    let videoId: String
    let initialSelectedIds: [String]   // from Video.selectedMaterials (snapshot)

    @State private var response: VideoMaterialsResponse?
    @State private var isLoading = false
    @State private var loadError: String?
    @State private var viewingMaterial: VideoMaterialItem?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let err = loadError {
                Text(err)
                    .font(.caption)
                    .foregroundColor(.red)
            }
            if isLoading && response == nil {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Loading materials…").font(.caption)
                }
                .foregroundColor(.secondary)
            }
            if let r = response {
                if r.selectedIds.isEmpty {
                    Text("No materials in context — the tutor will only use the video transcript.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                } else {
                    ForEach(selectedItems(in: r)) { item in
                        materialRow(item: item, isSelected: true)
                    }
                }
                // Show unselected materials (informational; user can flip on Mac)
                let unselected = unselectedItems(in: r)
                if !unselected.isEmpty {
                    DisclosureGroup("\(unselected.count) more material(s) not in context") {
                        ForEach(unselected) { item in
                            materialRow(item: item, isSelected: false)
                        }
                    }
                    .font(.caption)
                    .padding(.top, 4)
                }
            }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .task {
            await load()
        }
        .sheet(item: $viewingMaterial) { item in
            MaterialViewerSheet(item: item)
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "doc.text.fill")
                .foregroundColor(.indigo)
            Text("Materials in context")
                .font(.subheadline).bold()
            Spacer()
            if let r = response, !r.selectedIds.isEmpty {
                Text("\(r.selectedIds.count) · \(totalChars(r)) chars")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func materialRow(item: VideoMaterialItem, isSelected: Bool) -> some View {
        Button {
            viewingMaterial = item
        } label: {
            HStack(spacing: 8) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundColor(isSelected ? .indigo : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.filename)
                        .font(.caption)
                        .foregroundColor(.primary)
                        .lineLimit(1)
                    Text("\(item.sizeLabel)" + (item.charCount.map { " · \($0.formatted()) chars" } ?? ""))
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Image(systemName: "doc.text.magnifyingglass")
                    .foregroundColor(.secondary)
                    .font(.caption)
            }
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
    }

    // MARK: helpers

    private func selectedItems(in r: VideoMaterialsResponse) -> [VideoMaterialItem] {
        let lookup = Dictionary(uniqueKeysWithValues: r.available.map { ($0.materialId, $0) })
        return r.selectedIds.compactMap { lookup[$0] }
    }

    private func unselectedItems(in r: VideoMaterialsResponse) -> [VideoMaterialItem] {
        let sel = Set(r.selectedIds)
        return r.available.filter { !sel.contains($0.materialId) }
    }

    private func totalChars(_ r: VideoMaterialsResponse) -> String {
        let total = selectedItems(in: r).reduce(0) { $0 + ($1.charCount ?? 0) }
        return total.formatted()
    }

    private func load() async {
        // If the snapshot already says no selection, skip the network round-trip
        if initialSelectedIds.isEmpty { return }
        isLoading = true
        loadError = nil
        do {
            response = try await APIClient.shared.fetchVideoMaterials(videoId: videoId)
        } catch {
            loadError = "Couldn't load materials: \(error.localizedDescription)"
        }
        isLoading = false
    }
}

// MARK: - Viewer sheet

/// Shows the extracted plain text of one material. We don't render PDFs
/// inline (PDFKit adds ~5MB to the binary and only Mac users upload PDFs
/// in this MVP); the extracted text is the canonical source the LLM
/// reads, so this view is what the iOS user needs to see too.
struct MaterialViewerSheet: View {
    let item: VideoMaterialItem
    @Environment(\.dismiss) private var dismiss
    @State private var text: String = ""
    @State private var isLoading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                if isLoading {
                    ProgressView()
                        .padding(.top, 40)
                } else if let err = error {
                    Text(err)
                        .foregroundColor(.red)
                        .padding()
                } else if text.isEmpty {
                    Text("This material has no extracted text.")
                        .foregroundColor(.secondary)
                        .padding()
                } else {
                    Text(text)
                        .font(.system(.callout, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .padding()
                }
            }
            .navigationTitle(item.filename)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                await load()
            }
        }
    }

    private func load() async {
        do {
            text = try await APIClient.shared.fetchMaterialText(materialId: item.materialId)
        } catch {
            self.error = "Failed to load: \(error.localizedDescription)"
        }
        isLoading = false
    }
}