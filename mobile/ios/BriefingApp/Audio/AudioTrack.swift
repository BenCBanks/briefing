import Foundation

public enum AudioContentKind: String, Hashable, Sendable {
    case dailyEdition
    case weeklyEdition
    case explanation
    case fixture
}

public struct AudioContentIdentity: Hashable, Sendable {
    public let kind: AudioContentKind
    public let id: String
    public let revision: String
    public let scriptHash: String

    public init(kind: AudioContentKind, id: String, revision: String, scriptHash: String) {
        self.kind = kind
        self.id = id
        self.revision = revision
        self.scriptHash = scriptHash
    }
}

/// The stable identity of one recorded, script-matched audio revision.
public struct AudioTrack: Identifiable, Hashable, Sendable {
    public let id: String
    public let title: String
    public let kind: AudioContentKind
    public let revision: String
    public let scriptHash: String
    /// Bundle resource filename including extension; nil means audio is still pending.
    public let resourceFilename: String?
    public let chapters: [AudioChapter]

    public var contentIdentity: AudioContentIdentity {
        AudioContentIdentity(kind: kind, id: id, revision: revision, scriptHash: scriptHash)
    }

    public init(id: String, title: String, kind: AudioContentKind, revision: String,
                scriptHash: String, resourceFilename: String?,
                chapters: [AudioChapter] = []) {
        self.id = id
        self.title = title
        self.kind = kind
        self.revision = revision
        self.scriptHash = scriptHash
        self.resourceFilename = resourceFilename
        self.chapters = chapters
    }
}

public struct AudioChapter: Identifiable, Hashable, Sendable {
    public let id: String
    public let title: String
    public let startSeconds: TimeInterval

    public init(id: String, title: String, startSeconds: TimeInterval) {
        self.id = id
        self.title = title
        self.startSeconds = startSeconds
    }

    /// Returns metadata only when it forms a complete, ordered set of boundaries for this recording.
    public static func validated(_ chapters: [AudioChapter], duration: TimeInterval) -> [AudioChapter]? {
        guard duration.isFinite, duration > 0,
              let first = chapters.first, first.startSeconds == 0 else { return nil }
        var identifiers = Set<String>()
        var previousStart: TimeInterval = -1
        for chapter in chapters {
            let identifier = chapter.id.trimmingCharacters(in: .whitespacesAndNewlines)
            let title = chapter.title.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !identifier.isEmpty, !title.isEmpty,
                  identifiers.insert(identifier).inserted,
                  chapter.startSeconds.isFinite,
                  chapter.startSeconds >= 0,
                  chapter.startSeconds < duration,
                  chapter.startSeconds > previousStart else { return nil }
            previousStart = chapter.startSeconds
        }
        return chapters
    }
}

public enum AudioPlaybackStatus: Equatable {
    case idle
    case pending
    case missingResource
    case loading
    case ready
    case playing
    case paused
    case superseded
    case failed(String)

    public var isBusy: Bool { self == .loading }
    public var isPlaying: Bool { self == .playing }
}

public struct AudioPlaybackState: Equatable {
    public var track: AudioTrack?
    public var status: AudioPlaybackStatus = .idle
    public var duration: TimeInterval = 0
    public var position: TimeInterval = 0
    public var rate: Float = 1
    public var currentChapter: AudioChapter?

    public init(track: AudioTrack? = nil) { self.track = track }
    public var progress: Double {
        guard duration.isFinite, duration > 0 else { return 0 }
        return min(max(position / duration, 0), 1)
    }
}
