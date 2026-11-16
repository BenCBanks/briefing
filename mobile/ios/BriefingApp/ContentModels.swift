import Foundation

struct TopicPack: Decodable {
    let topics: [Topic]
}

struct Topic: Decodable, Identifiable {
    let id: String
    let label: String
    let scope: String
    let description: String
}

struct EditionPack: Decodable {
    let editions: [Edition]
}

struct Edition: Decodable, Identifiable {
    let id: String
    let kind: String
    let localDate: String?
    let weekStart: String?
    let revisionID: String?
    let scriptHash: String?
    let title: String
    let asOf: String?
    let durationMinutes: Int?
    let summary: String?
    let sections: [ArticleSection]
    let coverage: [Coverage]?
    let sourceIDs: [String]?
    let audio: AudioAsset?
    let superseded: Bool?
    let status: String?
    let withdrawn: Bool?
    let supersededBy: String?
    let withdrawnAt: String?
    let corrections: [EditionCorrection]?

    enum CodingKeys: String, CodingKey {
        case id, kind, title, summary, sections, coverage, audio, superseded, status, withdrawn, corrections
        case localDate = "local_date"
        case weekStart = "week_start"
        case revisionID = "revision_id"
        case scriptHash = "script_hash"
        case asOf = "as_of"
        case durationMinutes = "duration_minutes"
        case sourceIDs = "source_ids"
        case supersededBy = "superseded_by"
        case withdrawnAt = "withdrawn_at"
    }

    var isCurrent: Bool {
        superseded != true && withdrawn != true && supersededBy == nil && withdrawnAt == nil &&
            status?.lowercased() != "withdrawn" && status?.lowercased() != "superseded"
    }

    var materialCorrection: EditionCorrection? {
        corrections?.first(where: { $0.severity == "material" })
    }

    var readingSections: [ArticleSection] {
        let quiet = Set((coverage ?? []).filter { $0.state == "quiet" || $0.state == "limited" }.map(\.topicID))
        let visible = sections.filter { section in
            let topics = section.topicIDs ?? []
            return topics.isEmpty || topics.contains { !quiet.contains($0) }
        }
        return visible.isEmpty ? sections : visible
    }

    var readingMinutes: Int {
        let words = readingSections.reduce(0) { $0 + $1.body.split(whereSeparator: \.isWhitespace).count }
        return max(1, Int(ceil(Double(words) / 225)))
    }
}

struct EditionCorrection: Decodable, Identifiable {
    let id: String
    let severity: String
    let previousRevisionID: String?
    let revisionID: String?
    let correctedAt: String?
    let sectionIDs: [String]
    let whatChanged: String
    let whyItMatters: String
    let sourceIDs: [String]

    enum CodingKeys: String, CodingKey {
        case id, severity
        case previousRevisionID = "previous_revision_id"
        case revisionID = "revision_id"
        case correctedAt = "corrected_at"
        case sectionIDs = "section_ids"
        case whatChanged = "what_changed"
        case whyItMatters = "why_it_matters"
        case sourceIDs = "source_ids"
    }
}

struct ArticleSection: Decodable, Identifiable {
    let id: String
    let heading: String
    let body: String
    let topicIDs: [String]?
    let sourceIDs: [String]?

    enum CodingKeys: String, CodingKey {
        case id, heading, body
        case topicIDs = "topic_ids"
        case sourceIDs = "source_ids"
    }
}

struct Coverage: Decodable, Identifiable {
    let topicID: String
    let state: String
    let reason: String?
    var id: String { topicID }

    enum CodingKeys: String, CodingKey {
        case state, reason
        case topicID = "topic_id"
    }
}

struct DeepDivePack: Decodable {
    let deepDives: [DeepDive]
    enum CodingKeys: String, CodingKey { case deepDives = "deep_dives" }
}

struct DeepDive: Decodable, Identifiable {
    let id: String
    let topicID: String
    let title: String
    let reviewedAt: String?
    let revisionID: String?
    let scriptHash: String?
    let durationMinutes: Int?
    let summary: String?
    let sections: [ArticleSection]
    let audio: AudioAsset?
    let superseded: Bool?
    let status: String?
    let withdrawn: Bool?
    let supersededBy: String?
    let withdrawnAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, summary, sections, audio, superseded, status, withdrawn
        case topicID = "topic_id"
        case reviewedAt = "reviewed_at"
        case revisionID = "revision_id"
        case scriptHash = "script_hash"
        case durationMinutes = "duration_minutes"
        case supersededBy = "superseded_by"
        case withdrawnAt = "withdrawn_at"
    }

    var isCurrent: Bool {
        superseded != true && withdrawn != true && supersededBy == nil && withdrawnAt == nil &&
            status?.lowercased() != "withdrawn" && status?.lowercased() != "superseded"
    }
}

struct AudioAsset: Decodable {
    let state: AudioContentState
    let url: URL?
    let revisionID: String?
    let scriptHash: String?
    let durationSeconds: Int?
    let resourceFilename: String?
    let chapters: [AudioChapterMetadata]?

    enum CodingKeys: String, CodingKey {
        case state, url
        case revisionID = "revision_id"
        case scriptHash = "script_hash"
        case durationSeconds = "duration_seconds"
        case resourceFilename = "resource_filename"
        case chapters
    }

    var bundledFilename: String? {
        if let resourceFilename {
            return resourceFilename.split(separator: "/").last.map(String.init)
        }
        guard let url, url.scheme == nil,
              url.path.hasPrefix("/content/audio/") || url.path.hasPrefix("content/audio/") else { return nil }
        return url.lastPathComponent
    }

    var isBundledRecording: Bool { state == .recorded && bundledFilename != nil }

    func track(id: String, title: String, kind: AudioContentKind,
               revisionID: String?, scriptHash: String?) -> AudioTrack? {
        guard state == .recorded,
              let resourceFilename = bundledFilename,
              let revisionID,
              self.revisionID == revisionID,
              let scriptHash,
              self.scriptHash == scriptHash else { return nil }
        let chapterValues = (chapters ?? []).map(\.chapter)
        let validatedChapters: [AudioChapter]
        if chapterValues.isEmpty {
            validatedChapters = []
        } else if let durationSeconds,
                  let valid = AudioChapter.validated(chapterValues, duration: TimeInterval(durationSeconds)) {
            validatedChapters = valid
        } else {
            // Invalid chapter metadata must not disable the valid recording itself.
            validatedChapters = []
        }
        return AudioTrack(id: id, title: title, kind: kind,
                          revision: revisionID,
                          scriptHash: scriptHash,
                          resourceFilename: resourceFilename,
                          chapters: validatedChapters)
    }
}

struct AudioChapterMetadata: Decodable {
    let id: String
    let title: String
    let startSeconds: TimeInterval

    enum CodingKeys: String, CodingKey {
        case id, title
        case startSeconds = "start_seconds"
    }

    var chapter: AudioChapter { AudioChapter(id: id, title: title, startSeconds: startSeconds) }
}

enum AudioContentState: Equatable, Decodable {
    case recorded
    case pending
    case unavailable

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = AudioContentState(rawValue: value) ?? .unavailable
    }

    private init?(rawValue: String) {
        switch rawValue {
        case "recorded": self = .recorded
        case "pending": self = .pending
        default: return nil
        }
    }
}

struct SourcePack: Decodable {
    let sources: [Source]
}

struct Source: Decodable, Identifiable {
    let id: String
    let title: String
    let url: URL?
    let kind: String?
    let note: String?
    let publisher: String?
    let date: String?
    let supportedSectionIDs: [String]?
    let supportedSection: String?
    let scope: String?
    let availability: String?
    let evidence: String?

    enum CodingKeys: String, CodingKey {
        case id, title, url, kind, note, publisher, date, scope, availability, evidence
        case supportedSectionIDs = "supported_section_ids"
        case supportedSection = "supported_section"
    }
}

struct BriefingContent {
    let topics: [Topic]
    let editions: [Edition]
    let deepDives: [DeepDive]
    let sources: [Source]

    func currentEdition(kind: String) -> Edition? {
        let kindEditions = editions.filter { $0.kind == kind }
        let identifiers = Set(kindEditions.map(\.id))
        guard !identifiers.isEmpty else { return nil }
        if identifiers.count == 1, let onlyID = identifiers.first { return currentEdition(id: onlyID) }
        var latestByID: [String: String] = [:]
        for id in identifiers {
            let dates = kindEditions.filter { $0.id == id }.compactMap(Self.editionDateKey)
            guard let latest = dates.max() else { return nil }
            latestByID[id] = latest
        }
        guard let latestDate = latestByID.values.max() else { return nil }
        let latestIDs = latestByID.filter { $0.value == latestDate }.map { $0.key }
        guard latestIDs.count == 1 else { return nil }
        return currentEdition(id: latestIDs[0])
    }

    func currentEdition(id: String) -> Edition? {
        let candidates = editions.filter {
            $0.id == id && $0.superseded != true && $0.supersededBy == nil &&
                $0.status?.lowercased() != "superseded"
        }
        guard !candidates.isEmpty else { return nil }
        let previousRevisions = Set(candidates.flatMap { $0.corrections ?? [] }
            .compactMap(\.previousRevisionID))
        let heads = candidates.filter { !previousRevisions.contains($0.revisionID ?? "") }
        guard heads.count == 1, heads[0].isCurrent else { return nil }
        return heads[0]
    }

    /// Deep-dive packs may retain superseded revisions. Resolve only an unambiguous current item.
    func currentDeepDive(id: String) -> DeepDive? {
        let candidates = deepDives.filter {
            $0.id == id && $0.superseded != true && $0.supersededBy == nil &&
                $0.status?.lowercased() != "superseded"
        }
        guard candidates.count == 1, candidates[0].isCurrent else { return nil }
        return candidates[0]
    }

    func currentDeepDive(forTopic topicID: String) -> DeepDive? {
        let candidates = deepDives.filter {
            $0.topicID == topicID && $0.superseded != true && $0.supersededBy == nil &&
                $0.status?.lowercased() != "superseded"
        }
        guard candidates.count == 1, candidates[0].isCurrent else { return nil }
        return candidates[0]
    }

    var currentDeepDives: [DeepDive] {
        var seen = Set<String>()
        return deepDives.compactMap { dive in
            guard seen.insert(dive.id).inserted else { return nil }
            guard let current = currentDeepDive(id: dive.id),
                  currentDeepDive(forTopic: current.topicID)?.id == current.id else { return nil }
            return current
        }
    }

    var currentEditions: [Edition] {
        var seen = Set<String>()
        return Array(editions.reversed().compactMap { edition in
            guard seen.insert(edition.id).inserted,
                  let current = currentEdition(id: edition.id), current.kind == edition.kind else { return nil }
            return current
        }.reversed())
    }

    private static func editionDateKey(_ edition: Edition) -> String? {
        edition.localDate ?? edition.weekStart ?? edition.asOf
    }

    static func load(bundle: Bundle = .main) throws -> BriefingContent {
        let decoder = JSONDecoder()
        func decode<T: Decodable>(_ type: T.Type, file: String) throws -> T {
            guard let url = bundle.url(forResource: file, withExtension: "json") else {
                throw ContentError.missing(file)
            }
            return try decoder.decode(type, from: Data(contentsOf: url))
        }
        return BriefingContent(
            topics: try decode(TopicPack.self, file: "topics").topics,
            editions: try decode(EditionPack.self, file: "editions").editions,
            deepDives: try decode(DeepDivePack.self, file: "deep-dives").deepDives,
            sources: try decode(SourcePack.self, file: "sources").sources
        )
    }

    static func load(directory: URL) throws -> BriefingContent {
        let decoder = JSONDecoder()
        func decode<T: Decodable>(_ type: T.Type, file: String) throws -> T {
            let url = directory.appendingPathComponent(file).appendingPathExtension("json")
            guard FileManager.default.fileExists(atPath: url.path) else { throw ContentError.missing(file) }
            return try decoder.decode(type, from: Data(contentsOf: url))
        }
        return BriefingContent(
            topics: try decode(TopicPack.self, file: "topics").topics,
            editions: try decode(EditionPack.self, file: "editions").editions,
            deepDives: try decode(DeepDivePack.self, file: "deep-dives").deepDives,
            sources: try decode(SourcePack.self, file: "sources").sources
        )
    }
}

enum LocalItemStorageKey {
    static func saved(itemID: String) -> String { "briefing.saved.item.\(itemID)" }
}

enum ReaderProgressKey {
    static func finished(itemID: String, revisionID: String) -> String {
        "briefing.finished.\(itemID).\(revisionID)"
    }

    static func reading(itemID: String, revisionID: String, scriptHash: String? = nil) -> String {
        let base = "briefing.reading.\(itemID).\(revisionID)"
        guard let scriptHash else { return base }
        return "\(base).\(scriptHash)"
    }
}

enum ContentError: LocalizedError {
    case missing(String)
    var errorDescription: String? {
        switch self {
        case .missing(let file): return "The bundled \(file).json content file is missing."
        }
    }
}
