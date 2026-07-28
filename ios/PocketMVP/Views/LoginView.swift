import SwiftUI
import FirebaseAuth

// MARK: - Login view
//
// Shown when `FirebaseAuthService.shared.currentUser == nil`. Mirrors
// the web app's `login.html` UX:
//   - Tab segmented control: "Sign In" / "Sign Up"
//   - Email + password fields
//   - "Continue with Google" button (uses GoogleSignIn SDK)
//   - Inline error messages (friendlyMessage-for-error-code in the
//     service maps FirebaseAuthErrorCode to user-readable text)
//
// When Firebase is NOT configured (skipped because the iOS plist is
// invalid), the email/password + Google buttons are disabled and we
// show a "Skip sign-in (dev)" button at the bottom so the user can
// still see the app using the X-Dev-User-Id dev-auth fallback.
//
// v0.1.3-real-teaching v0.2 (Firebase auth on iOS).

struct LoginView: View {
    @StateObject private var auth = FirebaseAuthService.shared
    @State private var mode: Mode = .signIn
    @State private var email: String = ""
    @State private var password: String = ""

    enum Mode: String, CaseIterable, Identifiable {
        case signIn = "Sign In"
        case signUp = "Sign Up"
        var id: String { rawValue }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                // Mode picker
                Picker("Mode", selection: $mode) {
                    ForEach(Mode.allCases) { m in
                        Text(m.rawValue).tag(m)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)

                // Email + password
                VStack(alignment: .leading, spacing: 12) {
                    TextField("Email", text: $email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .disabled(!auth.isFirebaseConfigured)
                        .padding(12)
                        .background(Color(.secondarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 10))

                    SecureField("Password", text: $password)
                        .textContentType(mode == .signIn ? .password : .newPassword)
                        .disabled(!auth.isFirebaseConfigured)
                        .padding(12)
                        .background(Color(.secondarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .padding(.horizontal)

                // Error from last attempt
                if let err = auth.lastError {
                    Text(err)
                        .font(.subheadline)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                }

                // Submit button
                Button {
                    Task {
                        switch mode {
                        case .signIn:
                            await auth.signInWithEmail(email: email, password: password)
                        case .signUp:
                            await auth.signUpWithEmail(email: email, password: password)
                        }
                    }
                } label: {
                    HStack {
                        if auth.isWorking {
                            ProgressView().tint(.white)
                        }
                        Text(mode == .signIn ? "Sign In" : "Create Account")
                            .font(.headline)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.accentColor)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .disabled(!canSubmit)
                .padding(.horizontal)

                // Divider
                HStack(spacing: 12) {
                    Rectangle().fill(Color.secondary.opacity(0.3)).frame(height: 1)
                    Text("OR").font(.caption).foregroundStyle(.secondary)
                    Rectangle().fill(Color.secondary.opacity(0.3)).frame(height: 1)
                }
                .padding(.horizontal)

                // Google button
                Button {
                    Task { await auth.signInWithGoogle() }
                } label: {
                    HStack {
                        Image(systemName: "g.circle.fill")
                            .foregroundStyle(.red)
                        Text("Continue with Google")
                            .font(.subheadline)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color(.systemBackground))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .foregroundColor(.primary)
                }
                .disabled(auth.isWorking || !auth.isFirebaseConfigured)
                .padding(.horizontal)

                // Dev-only fallback: skip sign-in when Firebase isn't
                // configured. The X-Dev-User-Id header still works.
                if !auth.isFirebaseConfigured {
                    VStack(spacing: 8) {
                        Divider().padding(.vertical, 8)
                        Text("Dev auth fallback active")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Button {
                            // Fake-sign-in: just put a placeholder
                            // AuthUser so RootView routes to the app.
                            // The real API calls still use X-Dev-User-Id.
                            auth.signInDev()
                        } label: {
                            Label("Skip sign-in (dev)", systemImage: "wrench.adjustable")
                                .font(.subheadline)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                                .background(Color(.secondarySystemBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        .padding(.horizontal)
                    }
                }
            }
            .padding(.vertical)
        }
        .navigationTitle("Sign in to continue")
        .navigationBarTitleDisplayMode(.inline)
        .background(Color(.systemBackground))
        .task {
            auth.configureIfNeeded()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("📚 Pocket")
                .font(.largeTitle.weight(.bold))
                .padding(.horizontal)
            Text("Use email or Google to access your courses.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .padding(.horizontal)
        }
    }

    private var canSubmit: Bool {
        guard auth.isFirebaseConfigured else { return false }
        guard !auth.isWorking else { return false }
        guard !email.isEmpty, !password.isEmpty else { return false }
        guard email.contains("@") else { return false }
        guard password.count >= 6 else { return false }
        return true
    }
}

#Preview {
    NavigationStack {
        LoginView()
    }
}