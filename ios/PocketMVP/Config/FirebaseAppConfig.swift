import Foundation

// MARK: - Firebase init config
//
// Single switch for whether FirebaseApp.configure() should run. Set
// this to `false` until the iOS app is registered in Firebase Console
// AND the GoogleService-Info.plist has a valid iOS GOOGLE_APP_ID.
//
// When `skipConfigure == true`:
//   - App launches normally
//   - `FirebaseAuthService.isFirebaseConfigured` stays false
//   - LoginView shows a "Firebase not configured" message instead of
//     the sign-in form
//   - APIClient falls back to the `X-Dev-User-Id` header (when
//     `AppConfig.devUserId` is set) so the dev auth path still works
//
// When `skipConfigure == false`:
//   - FirebaseApp.configure() runs on app start
//   - If it crashes (e.g. invalid plist), the app crashes too. This
//     is unavoidable since Swift can't catch NSException. The fix is
//     to update the plist with a valid iOS GOOGLE_APP_ID.
//
// v0.1.3-real-teaching v0.2 (Firebase auth on iOS).
enum FirebaseAppConfig {
    /// Set to `false` after registering com.shoothigh.pocketmvp in
    /// Firebase Console + updating GoogleService-Info.plist with the
    /// real iOS GOOGLE_APP_ID.
    static let skipConfigure: Bool = true
}