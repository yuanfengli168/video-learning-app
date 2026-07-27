import Foundation

/// HTTP client for the /m/* sub-app API.
///
/// v0.1 design:
/// - All endpoints hit `AppConfig.baseURL`
/// - JSON encode/decode via ISO-8601 fractional seconds
/// - Bearer auth is delegated to whoever wires up the request (the pocket
///   sub-app currently uses Firebase session cookies; the iOS app would
///   need to forward them, which is deferred to v0.2)
/// - No retry, no caching, no background URL session. Just async/await
///   straight to the wire.
final class APIClient {
    static let shared = APIClient()

    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    private init() {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 30
        cfg.timeoutIntervalForResource = 120
        cfg.waitsForConnectivity = false
        self.session = URLSession(configuration: cfg)

        self.decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { d in
            let s = try d.singleValueContainer().decode(String.self)
            // Try common shapes; backend emits naive ISO ("2026-07-21T06:48:15"),
            // SQLite-native ("2026-07-21 06:48:15"), or ISO with fractional/Z.
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let dt = iso.date(from: s) { return dt }
            iso.formatOptions = [.withInternetDateTime]
            if let dt = iso.date(from: s) { return dt }
            let fmt = DateFormatter()
            fmt.locale = Locale(identifier: "en_US_POSIX")
            fmt.timeZone = TimeZone(identifier: "UTC")
            for pattern in [
                "yyyy-MM-dd HH:mm:ss.SSSSSS",
                "yyyy-MM-dd HH:mm:ss",
                "yyyy-MM-dd'T'HH:mm:ss",
            ] {
                fmt.dateFormat = pattern
                if let dt = fmt.date(from: s) { return dt }
            }
            throw DecodingError.dataCorruptedError(in: try d.singleValueContainer(),
                debugDescription: "Unrecognized date: \(s)")
        }
        self.encoder = JSONEncoder()
    }

    // MARK: - Endpoints

    /// GET /m/snapshot?since=<token>
    func fetchSnapshot(since: String? = nil) async throws -> Snapshot {
        var comps = URLComponents(url: AppConfig.baseURL.appendingPathComponent("/m/snapshot"),
                                  resolvingAgainstBaseURL: false)!
        if let since = since {
            comps.queryItems = [URLQueryItem(name: "since", value: since)]
        }
        return try await get(url: comps.url!, as: Snapshot.self)
    }

    /// POST /m/teach/{video_id}
    func startTeach(videoId: String) async throws -> TeachJobCreated {
        let url = AppConfig.baseURL.appendingPathComponent("/m/teach/\(videoId)")
        return try await post(url: url, body: Optional<String>.none, as: TeachJobCreated.self)
    }

    /// GET /m/teach/{video_id}/status?job_id=...
    func teachStatus(videoId: String, jobId: String) async throws -> TeachStatusResponse {
        var comps = URLComponents(
            url: AppConfig.baseURL.appendingPathComponent("/m/teach/\(videoId)/status"),
            resolvingAgainstBaseURL: false
        )!
        comps.queryItems = [URLQueryItem(name: "job_id", value: jobId)]
        return try await get(url: comps.url!, as: TeachStatusResponse.self)
    }

    /// GET /m/chunks/{video_id}
    func cachedChunks(videoId: String) async throws -> [Chunk] {
        let url = AppConfig.baseURL.appendingPathComponent("/m/chunks/\(videoId)")
        return try await get(url: url, as: [Chunk].self)
    }

    /// POST /m/chunk/{chunk_id}/done
    func markChunkDone(chunkId: String) async throws -> ChunkDone {
        let url = AppConfig.baseURL.appendingPathComponent("/m/chunk/\(chunkId)/done")
        return try await post(url: url, body: Optional<String>.none, as: ChunkDone.self)
    }

    /// GET /m/progress/{video_id}
    func fetchProgress(videoId: String) async throws -> ProgressSnapshot {
        let url = AppConfig.baseURL.appendingPathComponent("/m/progress/\(videoId)")
        return try await get(url: url, as: ProgressSnapshot.self)
    }

    // MARK: - Internal

    private func get<T: Decodable>(url: URL, as type: T.Type) async throws -> T {
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        Self.applyDevAuth(to: &req)
        let (data, resp) = try await session.data(for: req)
        try Self.assertOK(resp, data: data)
        return try decoder.decode(T.self, from: data)
    }

    private func post<Body: Encodable, T: Decodable>(url: URL, body: Body?, as type: T.Type) async throws -> T {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body = body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try encoder.encode(body)
        }
        Self.applyDevAuth(to: &req)
        let (data, resp) = try await session.data(for: req)
        try Self.assertOK(resp, data: data)
        return try decoder.decode(T.self, from: data)
    }

    /// Apply the dev-only `X-Dev-User-Id` header if configured. The backend
    /// only honors it when started with `POCKET_DEV_AUTH=1`, so this is
    /// safe to leave set in source — production backends will just 401
    /// instead of trusting the header.
    private static func applyDevAuth(to req: inout URLRequest) {
        if let uid = AppConfig.devUserId {
            req.setValue(uid, forHTTPHeaderField: "X-Dev-User-Id")
        }
    }

    private static func assertOK(_ resp: URLResponse, data: Data) throws {
        guard let http = resp as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.httpError(status: http.statusCode, body: body)
        }
    }
}

enum APIError: LocalizedError {
    case invalidResponse
    case httpError(status: Int, body: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "Invalid response from server"
        case .httpError(let s, let b): return "HTTP \(s): \(b.prefix(200))"
        }
    }
}
