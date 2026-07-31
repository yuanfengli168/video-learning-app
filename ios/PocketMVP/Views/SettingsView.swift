import SwiftUI

/// Settings screen (v0.1.1) — lets the user change the backend base URL
/// at runtime. Opened from the gear icon in CourseListView toolbar.
///
/// The base URL is persisted via @AppStorage and read by `AppConfig.baseURL`
/// on every API call, so changes take effect immediately on the next
/// sync / chat / health-check.
struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss

    /// Bound directly to `AppConfig.baseURLString` (same @AppStorage key).
    /// Editing this text field updates AppConfig.baseURL live.
    @AppStorage("pocket.baseURL") private var baseURL: String = AppConfig.baseURLString

    @State private var testStatus: TestStatus = .idle

    enum TestStatus: Equatable {
        case idle
        case testing
        case ok
        case fail(String)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Image(systemName: "network")
                            .foregroundStyle(.secondary)
                        TextField("https://your-mac:8443", text: $baseURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled(true)
                            .keyboardType(.URL)
                            .font(.system(.body, design: .monospaced))
                            .onSubmit { Task { await testConnection() } }
                    }
                    Button {
                        Task { await testConnection() }
                    } label: {
                        testButtonLabel
                    }
                    .disabled(testStatus == .testing || baseURL.isEmpty)
                } header: {
                    Text("Backend URL")
                } footer: {
                    Text("Where Pocket looks for your Mac's API. Changes save automatically.")
                }

                Section("Examples") {
                    Label("Simulator on same Mac", systemImage: "macbook")
                        .badge(Text("https://localhost:8443"))
                    Label("Same Wi-Fi", systemImage: "wifi")
                        .badge(Text("https://192.168.4.26:8443"))
                    Label("Tailscale (anywhere)", systemImage: "globe")
                        .badge(Text("https://<your-mac>.ts.net:8443"))
                        .font(.caption)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private var testButtonLabel: some View {
        switch testStatus {
        case .idle:
            Label("Test connection", systemImage: "antenna.radiowaves.left.and.right")
        case .testing:
            HStack { ProgressView(); Text("Testing…") }
        case .ok:
            Label("Connected", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .fail(let message):
            Label(message, systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red)
                .lineLimit(2)
        }
    }

    /// Hits `/api/health` on the entered URL. If it returns 200 we show green;
    /// anything else (timeout, cert error, connection refused, etc) is red.
    private func testConnection() async {
        testStatus = .testing
        guard let url = URL(string: baseURL)?.appendingPathComponent("/api/health") else {
            testStatus = .fail("Invalid URL")
            return
        }
        do {
            var req = URLRequest(url: url)
            req.timeoutInterval = 8
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                testStatus = .ok
            } else {
                let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                testStatus = .fail("HTTP \(code)")
            }
        } catch let error as URLError where error.code == .timedOut {
            testStatus = .fail("Timed out (8s)")
        } catch let error as URLError where error.code == .serverCertificateUntrusted
                                              || error.code == .secureConnectionFailed
                                              || error.code == .cancelled {
            testStatus = .fail("Cert error — install mkcert root CA on iPhone")
        } catch let error as URLError where error.code == .cannotConnectToHost
                                              || error.code == .cannotFindHost
                                              || error.code == .networkConnectionLost {
            testStatus = .fail("Can't reach server")
        } catch {
            testStatus = .fail(error.localizedDescription)
        }
    }
}

#Preview {
    SettingsView()
}
