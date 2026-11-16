import XCTest
import MediaPlayer
@testable import BriefingApp

@MainActor
final class AudioPlaybackTests: XCTestCase {
    func testDebugSeekSixSeedKeyRestoresTheChapterCaptureAtSixSeconds() async throws {
        let fixture = try XCTUnwrap(Bundle(for: AudioPlaybackTests.self)
            .url(forResource: "playback-fixture", withExtension: "wav"))
        let suiteName = "briefing.audio.seek-six-seed-test.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let track = AudioTrack(id: "simulator-fixture", title: "Playback test recording",
                               kind: .fixture, revision: "fixture-v1", scriptHash: "fixture-script-hash",
                               resourceFilename: "playback-fixture.wav")
        defaults.set(6.0, forKey: "briefing.audio.fixture.simulator-fixture.fixture-v1.fixture-script-hash")
        let controller = AudioController(defaults: defaults)
        controller.restore(track, from: fixture)
        try await waitUntil("debug seek seed restores at six seconds") {
            controller.state.status == .ready && abs(controller.state.position - 6) < 0.25
        }
        XCTAssertEqual(controller.state.position, 6, accuracy: 0.25)
    }

    private let testChapters = [
        AudioChapter(id: "opening", title: "Opening context", startSeconds: 0),
        AudioChapter(id: "developments", title: "Recent developments", startSeconds: 5),
        AudioChapter(id: "what-next", title: "What happens next", startSeconds: 12)
    ]

    func testRealPlayerAdvancesPausesResumesSeeksAndRestoresPausedPosition() async throws {
        let fixture = try XCTUnwrap(Bundle(for: AudioPlaybackTests.self)
            .url(forResource: "playback-fixture", withExtension: "wav"))
        let suiteName = "briefing.audio.playback-tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let track = AudioTrack(id: "simulator-fixture", title: "Playback test recording",
                               kind: .fixture, revision: "fixture-v1", scriptHash: "fixture-script-hash",
                               resourceFilename: "playback-fixture.wav")
        let firstLaunch = AudioController(defaults: defaults)
        firstLaunch.restore(track, from: fixture)
        try await waitUntil("fixture is ready") {
            firstLaunch.state.duration >= 19 && firstLaunch.state.status == .ready
        }

        firstLaunch.play()
        try await waitUntil("AVPlayer time advances") {
            firstLaunch.state.status == .playing && firstLaunch.state.position >= 1
        }

        for _ in 0..<5 {
            firstLaunch.pause()
            let pausedAt = firstLaunch.state.position
            try await Task.sleep(for: .milliseconds(700))
            XCTAssertEqual(firstLaunch.state.status, .paused)
            XCTAssertLessThanOrEqual(firstLaunch.state.position, pausedAt + 0.2)

            firstLaunch.play()
            try await waitUntil("AVPlayer resumes") {
                firstLaunch.state.status == .playing && firstLaunch.state.position >= pausedAt + 0.5
            }
        }

        firstLaunch.pause()
        firstLaunch.seek(to: 6)
        try await waitUntil("paused seek completes") {
            firstLaunch.state.status == .paused && abs(firstLaunch.state.position - 6) < 0.25
        }
        try await Task.sleep(for: .milliseconds(700))
        XCTAssertEqual(firstLaunch.state.status, .paused)
        XCTAssertLessThan(abs(firstLaunch.state.position - 6), 0.25)
        firstLaunch.appWillResignActive()

        // A fresh controller represents a relaunch. Restore must seek to the saved position
        // while leaving AVPlayer paused until the user explicitly presses Play.
        let relaunched = AudioController(defaults: defaults)
        relaunched.restoreLastSelectedTrack(from: [track], resourceURL: fixture)
        try await waitUntil("saved position restores without autoplay") {
            relaunched.state.status == .ready && abs(relaunched.state.position - 6) < 0.25
        }
        try await Task.sleep(for: .milliseconds(700))
        XCTAssertEqual(relaunched.state.status, .ready)
        XCTAssertLessThan(abs(relaunched.state.position - 6), 0.25)
    }

    func testChapterSeekPreservesPlaybackIntentAndReadingPlace() async throws {
        let fixture = try XCTUnwrap(Bundle(for: AudioPlaybackTests.self)
            .url(forResource: "playback-fixture", withExtension: "wav"))
        let suiteName = "briefing.audio.chapter-tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let track = AudioTrack(id: "fictional-edition", title: "Fictional chapter test",
                               kind: .fixture, revision: "fixture-v1", scriptHash: "fixture-script-hash",
                               resourceFilename: "playback-fixture.wav",
                               chapters: testChapters)
        let readingKey = "briefing.reading.\(track.id).\(track.revision)"
        defaults.set("fictional-section-b", forKey: readingKey)

        let controller = AudioController(defaults: defaults)
        controller.restore(track, from: fixture)
        try await waitUntil("three chapter fixture is ready") {
            controller.state.status == .ready && controller.chapters.count == 3
        }
        XCTAssertEqual(controller.state.currentChapter?.id, "opening")

        controller.seek(to: testChapters[1])
        try await waitUntil("paused chapter selection seeks to its boundary") {
            controller.state.status == .paused && controller.state.currentChapter?.id == "developments"
        }
        XCTAssertEqual(controller.state.position, 5, accuracy: 0.25)
        XCTAssertEqual(defaults.string(forKey: readingKey), "fictional-section-b")

        controller.play()
        try await waitUntil("player starts before selecting the final chapter") {
            controller.state.status == .playing && controller.state.position > 5
        }
        controller.seek(to: testChapters[2])
        try await waitUntil("playing chapter selection continues playback") {
            controller.state.status == .playing && controller.state.currentChapter?.id == "what-next"
        }
        XCTAssertGreaterThanOrEqual(controller.state.position, 12)
        XCTAssertLessThan(controller.state.position, 13)
        XCTAssertEqual(defaults.string(forKey: readingKey), "fictional-section-b")

        // The final chapter label remains selected when the recording completes.
        controller.seek(to: 19.8)
        try await waitUntil("final chapter remains selected at completion") {
            controller.state.status == .paused && controller.state.position >= 19 &&
                controller.state.currentChapter?.id == "what-next"
        }

        controller.play()
        try await waitUntil("replay returns chapter selection to the beginning") {
            controller.state.status == .playing && controller.state.currentChapter?.id == "opening"
        }

        let singleChapterTrack = AudioTrack(id: "fictional-short-edition", title: "Short edition",
                                            kind: .fixture, revision: "fixture-v1", scriptHash: "fixture-script-hash",
                                            resourceFilename: "playback-fixture.wav",
                                            chapters: [testChapters[0]])
        controller.restore(singleChapterTrack, from: fixture)
        try await waitUntil("single-chapter fixture is ready") {
            controller.state.status == .ready && controller.chapters.count == 1
        }
        XCTAssertFalse(controller.hasChapterNavigation)
    }

    func testCorrectionStopsActivePlaybackAndDoesNotCarrySecondsIntoReplacement() async throws {
        let fixture = try XCTUnwrap(Bundle(for: AudioPlaybackTests.self)
            .url(forResource: "playback-fixture", withExtension: "wav"))
        let suiteName = "briefing.audio.correction-tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let oldTrack = AudioTrack(id: "fictional-correction-edition", title: "Fictional pilot",
                                  kind: .dailyEdition, revision: "fictional-r1", scriptHash: "fictional-r1-hash",
                                  resourceFilename: "playback-fixture.wav",
                                  chapters: testChapters)
        let controller = AudioController(defaults: defaults)
        controller.restore(oldTrack, from: fixture)
        try await waitUntil("old recording ready") { controller.state.status == .ready }
        XCTAssertTrue(controller.hasChapterNavigation)
        controller.play()
        try await waitUntil("old recording playing") {
            controller.state.status == .playing && controller.state.position >= 0.5
        }
        controller.invalidateCurrentTrack()
        let stoppedAt = controller.state.position
        XCTAssertEqual(controller.state.status, .superseded)
        XCTAssertEqual(controller.state.track?.revision, "fictional-r1")
        XCTAssertFalse(controller.hasChapterNavigation)
        controller.setRate(1.25)
        XCTAssertNil(MPNowPlayingInfoCenter.default().nowPlayingInfo,
                     "Rate changes must not republish a superseded recording")
        controller.play()
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(controller.state.status, .superseded)
        XCTAssertLessThanOrEqual(controller.state.position, stoppedAt + 0.1)

        let replacement = AudioTrack(id: oldTrack.id, title: oldTrack.title,
                                     kind: .dailyEdition, revision: "fictional-r2", scriptHash: "fictional-r2-hash",
                                     resourceFilename: "playback-fixture.wav")
        controller.restoreLastSelectedTrack(from: [replacement], resourceURL: fixture)
        XCTAssertEqual(controller.state.status, .superseded, "Stale selection cannot restore as the corrected recording")
        controller.restore(replacement, from: fixture)
        try await waitUntil("replacement ready at its own position") {
            controller.state.status == .ready && controller.state.position < 0.25
        }
    }

    func testRestorationAndSavedPositionRequireTheCapturedScriptHash() async throws {
        let fixture = try XCTUnwrap(Bundle(for: AudioPlaybackTests.self)
            .url(forResource: "playback-fixture", withExtension: "wav"))
        let suiteName = "briefing.audio.hash-restore-tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let original = AudioTrack(id: "fictional-same-revision", title: "Fictional edition",
                                  kind: .dailyEdition, revision: "same-revision", scriptHash: "script-v1",
                                  resourceFilename: "playback-fixture.wav")
        let controller = AudioController(defaults: defaults)
        controller.restore(original, from: fixture)
        try await waitUntil("original hash recording is ready") { controller.state.status == .ready }
        controller.seek(to: 6)
        try await waitUntil("original hash position is saved") {
            controller.state.status == .paused && abs(controller.state.position - 6) < 0.25
        }
        controller.appWillResignActive()

        let correctedSameRevision = AudioTrack(id: original.id, title: original.title,
                                               kind: .dailyEdition, revision: original.revision,
                                               scriptHash: "script-v2", resourceFilename: "playback-fixture.wav")
        let oldKey = "briefing.audio.dailyEdition.\(original.id).\(original.revision).script-v1"
        let newKey = "briefing.audio.dailyEdition.\(original.id).\(original.revision).script-v2"
        XCTAssertEqual(defaults.double(forKey: oldKey), 6, accuracy: 0.25)
        XCTAssertEqual(defaults.double(forKey: newKey), 0, accuracy: 0.01,
                       "A changed script hash starts without the old recording's seconds")

        let mismatchedRestore = AudioController(defaults: defaults)
        mismatchedRestore.restoreLastSelectedTrack(from: [correctedSameRevision], resourceURL: fixture)
        XCTAssertNil(mismatchedRestore.state.track,
                     "A same-ID, same-revision candidate with a different script hash must not restore")

        controller.invalidateCurrentTrack()
        let afterInvalidation = AudioController(defaults: defaults)
        afterInvalidation.restoreLastSelectedTrack(from: [correctedSameRevision], resourceURL: fixture)
        XCTAssertNil(afterInvalidation.state.track,
                     "Invalidating old audio clears the persisted selection identity")
    }

    private func waitUntil(_ description: String, timeout: TimeInterval = 8,
                           condition: @MainActor () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(100))
        }
        XCTFail("Timed out waiting for \(description)")
    }
}
