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

    /// Polling interval for tutor job status. We poll (instead of using
    /// SSE) because the async job model is straightforward and adding
    /// SSE would complicate the deployment (proxy buffering, etc).
    static let teachStatusPollInterval: TimeInterval = 5.0

    /// Max polls before giving up. A 2-hour video can take 20-40 min to
    /// chunk through Ollama (10-30 chunks × ~1 min/chunk). 30 min × 60
    /// polls / 5 s = 360 polls. We pick a comfortable margin so the
    /// spinner doesn't time out for legitimately long videos.
    static let teachStatusMaxPolls: Int = 720  // 720 * 5s = 60 min

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
