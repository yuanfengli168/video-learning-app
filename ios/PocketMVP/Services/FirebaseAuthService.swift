import Foundation
import FirebaseCore
import FirebaseAuth
import GoogleSignIn
import os

private let log = Logger(subsystem: "com.shoothigh.pocketmvp", category: "auth")

// MARK: - Firebase auth service
//
// Singleton wrapper around FirebaseAuth that the iOS app uses for
// sign-in. Mirrors the web app's flow:
//
//   1. User picks "Google" or "Email/Password" on LoginView
//   2. FirebaseAuthService signs in (via GoogleSignIn SDK or
//      Auth.auth().signIn(withEmail:password:))
//   3. On success, the ID token is stored in Keychain
//   4. APIClient reads the token from Keychain and sends it as
//      `Authorization: Bearer <token>` to the backend
//   5. The backend's `get_current_user` verifies the token with
//      Firebase Admin SDK and returns the decoded UID
//
// The dev-only `X-Dev-User-Id` header bypass is still respected if
// `AppConfig.devUserId` is set (for offline UI development).
//
// v0.1.3-real-teaching v0.2 (Firebase auth on iOS).
@MainActor
final class FirebaseAuthService: ObservableObject {
    static let shared = FirebaseAuthService()

    /// nil = not signed in. Set after successful sign-in, cleared on sign-out.
    @Published private(set) var currentUser: AuthUser?

    /// True while a sign-in/sign-out is in flight. UI can disable
    /// buttons / show a spinner based on this.
    @Published private(set) var isWorking: Bool = false

    /// User-friendly message from the last sign-in attempt. Cleared on
    /// the next attempt.
    @Published var lastError: String?

    /// True after FirebaseApp.configure() has run. Lets the UI know the
    /// sign-in flow is available; before init we can only show "Firebase
    /// not configured".
    @Published private(set) var isFirebaseConfigured: Bool = false

    private init() {}

    // MARK: - Lifecycle

    /// Call once from app start (in `RootView` .task or App.init).
    /// Safe to call multiple times — FirebaseApp.configure() handles
    /// duplicates by returning the existing app.
    ///
    /// NOTE: Firebase's `+[FIRApp configure]` throws an Objective-C
    /// NSException when GoogleService-Info.plist is invalid (e.g. the
    /// iOS app wasn't registered in Firebase Console, so GOOGLE_APP_ID
    /// is the web app ID format). Swift's `try/catch` does NOT catch
    /// NSException — they crash the process.
    ///
    /// Until the iOS app is registered in Firebase Console and the
    /// real `GOOGLE_APP_ID` is in the plist, we **skip** the configure
    /// call entirely so the app launches normally. The UI still works
    /// because APIClient's `applyDevAuth` falls back to the
    /// `X-Dev-User-Id` header (controlled by `POCKET_DEV_AUTH=1` on
    /// the backend) when no Bearer token is in the keychain.
    ///
    /// When you have the iOS GOOGLE_APP_ID from Firebase Console:
    ///   1. Edit `ios/PocketMVP/Resources/GoogleService-Info.plist`
    ///   2. Replace `GOOGLE_APP_ID` with the iOS value
    ///      (`1:637126854377:ios:<random-hex>`)
    ///   3. Replace `CLIENT_ID` + `REVERSED_CLIENT_ID` (needed for Google
    ///      sign-in)
    ///   4. Set `FirebaseAppConfig.skipConfigure = false` below
    ///   5. Rebuild + reinstall
    func configureIfNeeded() {
        guard !isFirebaseConfigured else { return }

        if FirebaseAppConfig.skipConfigure {
            print("[FirebaseAuthService] FirebaseApp.configure() skipped (plist invalid). Dev auth fallback active.")
            self.lastError = "Firebase not configured (plist invalid). Sign-in is disabled. Dev auth fallback active."
            return
        }

        FirebaseApp.configure()
        isFirebaseConfigured = true

        // Listen for auth state changes — fires on sign-in, sign-out,
        // token refresh, and app foreground.
        Auth.auth().addStateDidChangeListener { [weak self] _, user in
            Task { @MainActor in
                guard let self else { return }
                if let user = user {
                    self.currentUser = AuthUser(
                        uid: user.uid,
                        email: user.email ?? "",
                        displayName: user.displayName,
                        isAnonymous: user.isAnonymous
                    )
                    // Cache the ID token for APIClient to use immediately.
                    if let token = try? await user.getIDToken() {
                        KeychainTokenStore.shared.save(token: token, uid: user.uid)
                    }
                } else {
                    self.currentUser = nil
                    KeychainTokenStore.shared.clear()
                }
            }
        }
    }

    // MARK: - Email / password sign-in

    func signInWithEmail(email: String, password: String) async {
        await performSignIn {
            let result = try await Auth.auth().signIn(withEmail: email, password: password)
            return result.user
        }
    }

    /// Create a new Firebase Auth account. Used for the "Sign Up" tab on
    /// the login screen (mirrors the web app's behavior).
    func signUpWithEmail(email: String, password: String) async {
        await performSignIn {
            let result = try await Auth.auth().createUser(withEmail: email, password: password)
            return result.user
        }
    }

    // MARK: - Google sign-in

    /// Sign in with Google via the native UI flow (SafariViewController
    /// for the OAuth consent). Mirrors the web app's popup flow.
    func signInWithGoogle() async {
        await performSignIn {
            // Step 1: get Google ID token via GoogleSignIn SDK
            guard let clientID = FirebaseApp.app()?.options.clientID else {
                throw AuthError.config("Missing Firebase clientID")
            }
            let config = GIDConfiguration(clientID: clientID)
            GIDSignIn.sharedInstance.configuration = config

            // Present the sign-in UI. The helper is async; we bridge it.
            let user: GIDGoogleUser = try await withCheckedThrowingContinuation { cont in
                GIDSignIn.sharedInstance.signIn(
                    withPresenting: UIApplication.shared.rootViewController()!,
                    hint: nil,
                    additionalScopes: ["email"]
                ) { result, error in
                    // Detailed error logging so we can diagnose silent
                    // OAuth failures (the OAuth sheet appears then
                    // dismisses without UI feedback).
                    if let error = error {
                        let nsError = error as NSError
                        log.error("GIDSignIn.signIn error: domain=\(nsError.domain) code=\(nsError.code) userInfo=\(nsError.userInfo)")
                        log.error("  localizedDescription: \(nsError.localizedDescription)")
                        if let underlying = nsError.userInfo[NSUnderlyingErrorKey] as? NSError {
                            log.error("  underlying: domain=\(underlying.domain) code=\(underlying.code) desc=\(underlying.localizedDescription)")
                        }
                        cont.resume(throwing: error)
                    } else if let result = result {
                        log.info("GIDSignIn.signIn OK: user=\(result.user.profile?.email ?? "?")")
                        cont.resume(returning: result.user)
                    } else {
                        log.error("GIDSignIn.signIn returned nil result AND nil error")
                        cont.resume(throwing: AuthError.unknown)
                    }
                }
            }

            guard let idToken = user.idToken?.tokenString else {
                throw AuthError.config("Google returned no idToken")
            }
            let accessToken = user.accessToken.tokenString

            // Step 2: exchange Google tokens for a Firebase credential
            let credential = GoogleAuthProvider.credential(
                withIDToken: idToken,
                accessToken: accessToken
            )
            let result = try await Auth.auth().signIn(with: credential)
            return result.user
        }
    }

    // MARK: - Sign out

    func signOut() {
        if isFirebaseConfigured {
            do {
                try Auth.auth().signOut()
            } catch {
                lastError = "Sign out failed: \(error.localizedDescription)"
            }
            // Full GoogleSignIn teardown so the next "Continue with
            // Google" actually shows the account picker:
            //   1. signOut() — clears the SDK session
            //   2. disconnect() — revokes the OAuth grant server-side
            //   3. clear configuration — drops the cached clientID so
            //      GIDSignIn re-presents the consent UI next time
            // Without step 2 + 3, iOS auto-signs-in with the cached
            // Google account (no picker shown).
            GIDSignIn.sharedInstance.signOut()
            GIDSignIn.sharedInstance.disconnect { _ in
                GIDSignIn.sharedInstance.configuration = nil
            }
        }
        currentUser = nil
        KeychainTokenStore.shared.clear()
    }

    // MARK: - Dev-only sign-in (when Firebase isn't configured)

    /// Fakes a sign-in for the X-Dev-User-Id fallback path. Only callable
    /// when Firebase isn't configured (otherwise real sign-in would be
    /// the right path). The placeholder `AuthUser` has UID
    /// `"dev-user"` and a fake email; the backend doesn't see this — it
    /// still uses the X-Dev-User-Id header (controlled by
    /// AppConfig.devUserId + POCKET_DEV_AUTH=1) when the iOS app has
    /// no Bearer token in the keychain.
    func signInDev() {
        guard !isFirebaseConfigured else {
            lastError = "Firebase is configured — use real sign-in instead."
            return
        }
        let devUid = AppConfig.devUserId ?? "dev-user"
        currentUser = AuthUser(
            uid: devUid,
            email: "dev@local",
            displayName: "Dev user (Firebase off)",
            isAnonymous: true
        )
    }

    // MARK: - Internal

    /// Common flow for any sign-in method. Sets isWorking, clears errors,
    /// updates currentUser on success.
    private func performSignIn(_ work: @escaping () async throws -> FirebaseAuth.User) async {
        guard isFirebaseConfigured else {
            lastError = "Firebase is not configured yet. Please restart the app."
            return
        }
        isWorking = true
        lastError = nil

        // Race the sign-in work against a 30s timeout so the spinner
        // can never get permanently stuck (e.g. GIDSignIn's OAuth
        // callback never fires if there's no Google account on the
        // device and the continuation is left dangling).
        do {
            let result = try await withThrowingTaskGroup(of: FirebaseAuth.User?.self) { group in
                group.addTask {
                    let user = try await work()
                    return user
                }
                group.addTask {
                    try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                    return nil  // timeout sentinel
                }
                // Wait for first non-nil result
                for try await value in group {
                    if let value = value {
                        group.cancelAll()
                        return value
                    }
                }
                throw NSError(
                    domain: "PocketMVP.auth",
                    code: -1,
                    userInfo: [NSLocalizedDescriptionKey: "Sign-in timed out after 30s. Please try again."]
                )
            }

            currentUser = AuthUser(
                uid: result.uid,
                email: result.email ?? "",
                displayName: result.displayName,
                isAnonymous: result.isAnonymous
            )
            if let token = try? await result.getIDToken() {
                KeychainTokenStore.shared.save(token: token, uid: result.uid)
            }
        } catch let error as NSError {
            log.error("performSignIn failed: \(error.localizedDescription) (domain=\(error.domain) code=\(error.code))")
            lastError = friendlyMessage(for: error)
        } catch {
            log.error("performSignIn unknown error: \(error.localizedDescription)")
            lastError = error.localizedDescription
        }

        isWorking = false
    }

    /// Convert FirebaseAuth error codes into messages the user can act on.
    /// Mirrors the web app's `enabledProviders: ['google', 'email']`
    /// behavior in login.html.
    private func friendlyMessage(for error: NSError) -> String {
        guard let code = AuthErrorCode.Code(rawValue: error.code) else {
            return error.localizedDescription
        }
        switch code {
        case .invalidEmail:        return "That email address is not valid."
        case .missingEmail:        return "Please enter your email."
        case .wrongPassword:       return "Wrong email or password. Please try again."
        case .userNotFound:        return "No account exists for that email. Try Sign Up instead."
        case .emailAlreadyInUse:   return "That email is already registered. Try Sign In instead."
        case .weakPassword:        return "Password is too weak. Use at least 6 characters."
        case .userDisabled:        return "This account has been disabled."
        case .networkError:        return "Network error. Check your connection and try again."
        case .tooManyRequests:     return "Too many attempts. Try again in a few minutes."
        default:                   return error.localizedDescription
        }
    }
}

// MARK: - AuthUser

/// Lightweight view of the Firebase user for the iOS UI. Stored in
/// `FirebaseAuthService.currentUser` so SwiftUI views can react to
/// sign-in / sign-out via @Published.
struct AuthUser: Equatable {
    let uid: String
    let email: String
    let displayName: String?
    let isAnonymous: Bool

    var displayLabel: String {
        if let dn = displayName, !dn.isEmpty { return dn }
        if !email.isEmpty { return email }
        return uid.prefix(8) + "…"
    }
}

// MARK: - Errors

enum AuthError: LocalizedError {
    case config(String)
    case unknown

    var errorDescription: String? {
        switch self {
        case .config(let m): return m
        case .unknown:      return "An unknown sign-in error occurred."
        }
    }
}

// MARK: - UIApplication helper

private extension UIApplication {
    /// Walk the active window scene to find the topmost view controller.
    /// Needed for the Google sign-in presenter's `presentingViewController`.
    func rootViewController() -> UIViewController? {
        connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first { $0.isKeyWindow }?
            .rootViewController
    }
}