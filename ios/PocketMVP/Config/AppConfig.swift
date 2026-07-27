import Foundation

/// Single source of truth for the iOS app's backend URL.
///
/// v0.1: hardcoded to localhost (HTTPS via mkcert). The iOS Simulator on
/// the same Mac can hit this directly. For a real iPhone on the LAN, edit
/// this to your Mac's LAN IP (e.g. https://192.168.1.42:8443) in v0.2.
///
/// NEVER hardcode the URL anywhere else in the app — always read from here.
enum AppConfig {
    static let baseURL: URL = URL(string: "https://localhost:8443")!

    /// Polling interval for tutor job status.
    static let teachStatusPollInterval: TimeInterval = 2.0

    /// Max polls before giving up and showing a "still working" UI.
    static let teachStatusMaxPolls: Int = 60  // 60 * 2s = 2 min

    /// When true, the app reads `sample_snapshot.json` from the bundle
    /// instead of calling the API. Lets us develop the UI without the
    /// backend running. Set to false to talk to the live API.
    static let useSampleData: Bool = false

    /// Dev-only auth bypass. When non-nil, the iOS app sends an
    /// `X-Dev-User-Id` header on every request, and the backend (when
    /// started with `POCKET_DEV_AUTH=1`) trusts it as the authenticated
    /// user. Set to nil in production. See `app/pocket/dev_auth.py`.
    static let devUserId: String? = "ltLtLQzr3nOr2hQKdeTxYnIOYYN2"
}
