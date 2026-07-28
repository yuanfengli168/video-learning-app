import Foundation
import Security

// MARK: - Keychain token store
//
// Persists the Firebase ID token + UID across app launches using the
// iOS Keychain. The token is the only piece of state that needs to
// survive a relaunch — FirebaseAuth itself handles refresh, but we
// need the token available synchronously for the first API call.
//
// Why Keychain (not UserDefaults)?
//   - Tokens are credentials; UserDefaults is plain-text
//   - Keychain survives app reinstalls (with iCloud Keychain) but not
//     full device wipes
//   - On the simulator, Keychain persists per-simulator (close to
//     device behavior)
//
// Thread safety: Keychain APIs are thread-safe; we keep this as a
// stateless singleton.

final class KeychainTokenStore {
    static let shared = KeychainTokenStore()
    private init() {}

    // Service identifier (groups related items together in the keychain)
    private let service = "com.shoothigh.pocketmvp.auth"

    // Two distinct keys — one for the token, one for the UID
    private let tokenKey = "firebase_id_token"
    private let uidKey = "firebase_uid"

    // MARK: - Public API

    /// Save the Firebase ID token + UID to Keychain. Replaces any prior
    /// value. Cheap; safe to call from sign-in completion handlers.
    func save(token: String, uid: String) {
        save(value: token, for: tokenKey)
        save(value: uid, for: uidKey)
    }

    /// Synchronously read the cached ID token. Returns nil if no token
    /// is cached (e.g. fresh install before any sign-in). The token is
    /// refreshed by FirebaseAuth at most every hour, so on app launch
    /// we read this; if FirebaseAuth later says the token is invalid,
    /// APIClient will get a 401 and the LoginView will be shown.
    func loadToken() -> String? {
        load(for: tokenKey)
    }

    /// Last cached UID. Same semantics as loadToken.
    func loadUid() -> String? {
        load(for: uidKey)
    }

    /// Remove both items. Called on sign-out.
    func clear() {
        delete(for: tokenKey)
        delete(for: uidKey)
    }

    // MARK: - Private helpers

    private func save(value: String, for key: String) {
        guard let data = value.data(using: .utf8) else { return }
        // Always delete before save — SecItemAdd fails with
        // errSecDuplicateItem if the key already exists.
        delete(for: key)
        let query: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  service,
            kSecAttrAccount as String:  key,
            kSecValueData as String:    data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    private func load(for key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  service,
            kSecAttrAccount as String:  key,
            kSecReturnData as String:   true,
            kSecMatchLimit as String:   kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let str = String(data: data, encoding: .utf8) else {
            return nil
        }
        return str
    }

    private func delete(for key: String) {
        let query: [String: Any] = [
            kSecClass as String:        kSecClassGenericPassword,
            kSecAttrService as String:  service,
            kSecAttrAccount as String:  key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}