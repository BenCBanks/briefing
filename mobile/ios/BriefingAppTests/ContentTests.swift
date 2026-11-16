import XCTest
@testable import BriefingApp

final class ContentTests: XCTestCase {
    func testBundledPreviewHasCompleteCoverageAndExplanations() throws {
        let content = try BriefingContent.load()
        XCTAssertEqual(content.topics.count, 5)
        XCTAssertEqual(Set(content.editions.map(\.kind)), Set(["daily", "weekly"]))
        XCTAssertEqual(Set(content.deepDives.map(\.topicID)), Set(content.topics.map(\.id)))
        for edition in content.editions {
            XCTAssertEqual(Set(edition.coverage?.map(\.topicID) ?? []), Set(content.topics.map(\.id)))
            XCTAssertFalse(edition.sections.isEmpty)
        }
    }

    func testOrdinaryBundledEditionsKeepAudioPendingAndExposeNoPlayTrack() throws {
        let content = try BriefingContent.load()
        for edition in content.editions {
            XCTAssertEqual(edition.audio?.state, .pending)
            XCTAssertNil(edition.audio?.track(id: edition.id, title: edition.title,
                                              kind: edition.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                                              revisionID: edition.revisionID, scriptHash: edition.scriptHash))
        }
        for dive in content.deepDives {
            XCTAssertEqual(dive.audio?.state, .pending)
            XCTAssertNil(dive.audio?.track(id: dive.id, title: dive.title,
                                           kind: .explanation,
                                           revisionID: dive.revisionID, scriptHash: dive.scriptHash))
        }
    }

    func testAudioChapterMetadataDecodesAndRejectsInvalidBoundaries() throws {
        let valid = try decodeAudioAsset(chapters: """
          [{"id":"opening","title":"Opening","start_seconds":0},
           {"id":"developments","title":"Developments","start_seconds":5},
           {"id":"what-next","title":"What happens next","start_seconds":12}]
          """)
        let track = try XCTUnwrap(valid.track(id: "fictional-edition", title: "Fictional edition",
                                             kind: .dailyEdition, revisionID: "rev-1", scriptHash: "script-hash"))
        XCTAssertEqual(track.scriptHash, "script-hash")
        XCTAssertEqual(track.kind, .dailyEdition)
        XCTAssertEqual(track.chapters.map(\.id), ["opening", "developments", "what-next"])
        XCTAssertEqual(track.chapters.map(\.startSeconds), [0, 5, 12])

        let unordered = try decodeAudioAsset(chapters: """
          [{"id":"opening","title":"Opening","start_seconds":0},
           {"id":"later","title":"Later","start_seconds":12},
           {"id":"earlier","title":"Earlier","start_seconds":5}]
          """)
        let unorderedTrack = try XCTUnwrap(unordered.track(id: "fictional-edition", title: "Fictional edition",
                                                           kind: .dailyEdition, revisionID: "rev-1", scriptHash: "script-hash"))
        XCTAssertTrue(unorderedTrack.chapters.isEmpty)

        let duplicate = [
            AudioChapter(id: "same", title: "Opening", startSeconds: 0),
            AudioChapter(id: "same", title: "Later", startSeconds: 5)
        ]
        XCTAssertNil(AudioChapter.validated(duplicate, duration: 20))
        XCTAssertNil(AudioChapter.validated([
            AudioChapter(id: "opening", title: "Opening", startSeconds: 0),
            AudioChapter(id: "outside", title: "Outside recording", startSeconds: 20)
        ], duration: 20))
    }

    func testCorrectionRevisionsKeepStableItemAndSectionIdentityButUseCurrentRevision() throws {
        let json = """
        {"editions":[
          {"id":"fictional-edition","kind":"daily","revision_id":"r1","title":"Pilot",
           "superseded":true,"script_hash":"old-hash",
           "sections":[{"id":"pilot-count","heading":"Count","body":"120 participants"}],
           "audio":{"state":"recorded","revision_id":"r1","script_hash":"old-hash","url":"old.wav"}},
          {"id":"fictional-edition","kind":"daily","revision_id":"r2","title":"Pilot",
           "script_hash":"new-hash",
           "sections":[{"id":"pilot-count","heading":"Count","body":"12 participants"}],
           "audio":{"state":"pending","revision_id":"r2","script_hash":"new-hash","url":null,"chapters":[]},
           "corrections":[{"id":"corr-1","severity":"material","previous_revision_id":"r1",
             "revision_id":"r2","corrected_at":"2026-11-16T03:00:00Z","section_ids":["pilot-count"],
             "what_changed":"120 became 12","why_it_matters":"The sample is smaller","source_ids":["record-1"]}]}
        ]}
        """
        let pack = try JSONDecoder().decode(EditionPack.self, from: Data(json.utf8))
        let content = BriefingContent(topics: [], editions: pack.editions, deepDives: [], sources: [])
        let current = try XCTUnwrap(content.currentEdition(kind: "daily"))
        XCTAssertEqual(current.id, "fictional-edition")
        XCTAssertEqual(current.revisionID, "r2")
        XCTAssertEqual(current.sections.first?.id, "pilot-count")
        XCTAssertEqual(content.currentEditions.count, 1)
        XCTAssertEqual(current.materialCorrection?.previousRevisionID, "r1")
        XCTAssertEqual(current.materialCorrection?.sectionIDs, ["pilot-count"])
        XCTAssertNil(current.audio?.track(id: current.id, title: current.title,
                                          kind: .dailyEdition,
                                          revisionID: current.revisionID, scriptHash: current.scriptHash))
        let staleRecording = try decodeAudioAsset(chapters: "[]")
        XCTAssertNil(staleRecording.track(id: current.id, title: current.title,
                                          kind: .dailyEdition,
                                          revisionID: "r2", scriptHash: "different-script-hash"))
        let savedBeforeCorrection = LocalItemStorageKey.saved(itemID: "fictional-edition")
        let savedAfterCorrection = LocalItemStorageKey.saved(itemID: current.id)
        XCTAssertEqual(savedBeforeCorrection, savedAfterCorrection)
        XCTAssertNotEqual(pack.editions[0].revisionID, pack.editions[1].revisionID)
        XCTAssertNotEqual(ReaderProgressKey.finished(itemID: current.id, revisionID: "r1"),
                          ReaderProgressKey.finished(itemID: current.id, revisionID: "r2"))
        XCTAssertNotEqual(ReaderProgressKey.reading(itemID: current.id, revisionID: "r1"),
                          ReaderProgressKey.reading(itemID: current.id, revisionID: "r2"))
        XCTAssertNotEqual(ReaderProgressKey.reading(itemID: current.id, revisionID: "same", scriptHash: "hash-a"),
                          ReaderProgressKey.reading(itemID: current.id, revisionID: "same", scriptHash: "hash-b"))
    }

    func testTranscriptTargetUsesCurrentWeeklyRevisionAndMatchesExplanationByRecording() throws {
        let weeklyJSON = """
        {"editions":[
          {"id":"fictional-weekly","kind":"weekly","revision_id":"r2","script_hash":"new-hash",
           "title":"Current recap","sections":[{"id":"new","heading":"Current","body":"Current text"}],
           "corrections":[{"id":"correction","severity":"material","previous_revision_id":"r1","section_ids":["new"],"what_changed":"Corrected text","why_it_matters":"It matters","source_ids":[]}],
           "audio":{"state":"pending","revision_id":"r2","script_hash":"new-hash"}},
          {"id":"fictional-weekly","kind":"weekly","revision_id":"r1","script_hash":"old-hash",
           "title":"Old recap","sections":[{"id":"old","heading":"Old","body":"Old text"}],
           "audio":{"state":"recorded","revision_id":"r1","script_hash":"old-hash","resource_filename":"old.wav"}}
        ]}
        """
        let weeklyPack = try JSONDecoder().decode(EditionPack.self, from: Data(weeklyJSON.utf8))
        let weeklyContent = BriefingContent(topics: [], editions: weeklyPack.editions, deepDives: [], sources: [])
        XCTAssertEqual(weeklyContent.currentEdition(kind: "weekly")?.revisionID, "r2")
        let oldRecording = AudioTrack(id: "fictional-weekly", title: "Old recap", kind: .weeklyEdition,
                                      revision: "r1", scriptHash: "old-hash",
                                      resourceFilename: "old.wav")
        guard case .edition(let current)? = TranscriptTarget.resolve(track: oldRecording, in: weeklyContent) else {
            return XCTFail("Expected the weekly transcript destination")
        }
        XCTAssertEqual(current.revisionID, "r2")
        XCTAssertEqual(current.sections.first?.body, "Current text")

        let topic = Topic(id: "fictional-topic", label: "Fictional topic", scope: "Prototype", description: "Invented")
        let dive = DeepDive(id: "fictional-explanation", topicID: topic.id, title: "Current explanation",
                            reviewedAt: nil, revisionID: "dive-r2", scriptHash: "dive-hash",
                            durationMinutes: nil, summary: nil,
                            sections: [ArticleSection(id: "section", heading: "Current", body: "Explanation", topicIDs: nil, sourceIDs: nil)],
                            audio: nil, superseded: nil, status: nil, withdrawn: nil,
                            supersededBy: nil, withdrawnAt: nil)
        // The loaded content confirms the recording's exact script and resource.
        let audio = try JSONDecoder().decode(AudioAsset.self, from: Data("""
        {"state":"recorded","revision_id":"dive-r2","script_hash":"dive-hash",
         "resource_filename":"explanation.wav","chapters":[]}
        """.utf8))
        let recordedDive = DeepDive(id: dive.id, topicID: dive.topicID, title: dive.title,
                                    reviewedAt: dive.reviewedAt, revisionID: dive.revisionID,
                                    scriptHash: dive.scriptHash, durationMinutes: dive.durationMinutes,
                                    summary: dive.summary, sections: dive.sections,
                                    audio: audio, superseded: nil, status: nil, withdrawn: nil,
                                    supersededBy: nil, withdrawnAt: nil)
        let matchingContent = BriefingContent(topics: [topic], editions: [], deepDives: [recordedDive], sources: [])
        let matchingRecording = AudioTrack(id: dive.id, title: dive.title, kind: .explanation,
                                           revision: "rev-1", scriptHash: "old-dive-hash",
                                           resourceFilename: "fictional.wav")
        let currentRecording = AudioTrack(id: dive.id, title: dive.title, kind: .explanation,
                                          revision: "dive-r2", scriptHash: "dive-hash",
                                          resourceFilename: "explanation.wav")
        guard case .explanation(let resolvedTopic, let resolvedDiveID)? = TranscriptTarget.resolve(track: currentRecording, in: matchingContent) else {
            return XCTFail("Expected the explanation's topic destination")
        }
        XCTAssertEqual(resolvedTopic.id, topic.id)
        XCTAssertEqual(resolvedDiveID, dive.id)
        guard case .explanation(let correctedTopic, let correctedDiveID)? = TranscriptTarget.resolve(track: matchingRecording, in: matchingContent) else {
            return XCTFail("An older recording should resolve by stable item ID to current explanation text")
        }
        XCTAssertEqual(correctedTopic.id, topic.id)
        XCTAssertEqual(correctedDiveID, dive.id)
        XCTAssertNil(TranscriptTarget.resolve(track: AudioTrack(id: "absent", title: "Absent", kind: .weeklyEdition,
                                                                revision: "r1", scriptHash: "x-hash", resourceFilename: "x.wav"), in: weeklyContent))
    }

    func testOlderDailyTranscriptResolvesToItsOwnEditionInsteadOfToday() throws {
        let json = """
        {"editions":[
          {"id":"fictional-today-daily","kind":"daily","revision_id":"today-r1",
           "script_hash":"today-hash","title":"Today",
           "sections":[{"id":"today-section","heading":"Today","body":"Today text"}],
           "local_date":"2026-11-16","audio":{"state":"pending","revision_id":"today-r1","script_hash":"today-hash"}},
          {"id":"fictional-older-daily","kind":"daily","revision_id":"older-r1",
           "script_hash":"script-hash","title":"Older daily","local_date":"2026-11-15",
           "sections":[{"id":"older-section","heading":"Older","body":"Older text"}],
           "audio":{"state":"recorded","revision_id":"older-r1","script_hash":"script-hash",
                    "resource_filename":"fictional.wav"}}
        ]}
        """
        let pack = try JSONDecoder().decode(EditionPack.self, from: Data(json.utf8))
        let content = BriefingContent(topics: [], editions: pack.editions, deepDives: [], sources: [])
        let oldTrack = AudioTrack(id: "fictional-older-daily", title: "Older daily", kind: .dailyEdition,
                                  revision: "older-r1", scriptHash: "script-hash", resourceFilename: "fictional.wav")
        guard case .edition(let destination)? = TranscriptTarget.resolve(track: oldTrack, in: content) else {
            return XCTFail("Expected the selected older daily edition")
        }
        let today = try XCTUnwrap(content.currentEdition(kind: "daily"))
        XCTAssertEqual(destination.id, "fictional-older-daily")
        XCTAssertEqual(today.id, "fictional-today-daily")
        XCTAssertNotEqual(destination.id, today.id)
        let oldIdentity = AudioContentIdentity(kind: .dailyEdition, id: destination.id,
                                               revision: destination.revisionID!, scriptHash: destination.scriptHash!)
        let todayIdentity = AudioContentIdentity(kind: .dailyEdition, id: today.id,
                                                 revision: today.revisionID!, scriptHash: today.scriptHash!)
        XCTAssertFalse(canDismissTranscriptWithoutNavigation(track: oldTrack, destination: oldIdentity,
                                                             visibleReader: todayIdentity))
        XCTAssertTrue(canDismissTranscriptWithoutNavigation(track: oldTrack, destination: oldIdentity,
                                                            visibleReader: oldIdentity))
        XCTAssertTrue(canDismissTranscriptWithoutNavigation(track: oldTrack, destination: oldIdentity,
                                                            visibleReader: nil, originReader: oldIdentity),
                      "The reader captured before presenting the player remains the exact return destination")
        XCTAssertFalse(canDismissTranscriptWithoutNavigation(track: oldTrack, destination: oldIdentity,
                                                             visibleReader: nil, originReader: todayIdentity))
        let mismatchedHash = AudioContentIdentity(kind: .dailyEdition, id: destination.id,
                                                  revision: destination.revisionID!, scriptHash: "different-hash")
        XCTAssertFalse(canDismissTranscriptWithoutNavigation(track: oldTrack, destination: mismatchedHash,
                                                             visibleReader: oldIdentity))
    }

    func testExplanationTranscriptUsesUniqueCurrentRevisionIndependentOfArrayOrder() throws {
        let json = #"""
        {"deep_dives":[
          {"id":"fictional-dive","topic_id":"fictional-topic","revision_id":"r2","script_hash":"current-hash","title":"Current explanation","sections":[]},
          {"id":"fictional-dive","topic_id":"fictional-topic","revision_id":"r1","script_hash":"old-hash","title":"Superseded explanation","superseded":true,"sections":[]},
          {"id":"withdrawn-status","topic_id":"topic-status","revision_id":"r1","title":"Withdrawn status","status":"withdrawn","sections":[]},
          {"id":"withdrawn-flag","topic_id":"topic-flag","revision_id":"r1","title":"Withdrawn flag","withdrawn":true,"sections":[]},
          {"id":"superseded-link","topic_id":"topic-link","revision_id":"r1","title":"Superseded link","superseded_by":"r2","sections":[]},
          {"id":"withdrawn-date","topic_id":"topic-date","revision_id":"r1","title":"Withdrawn date","withdrawn_at":"2026-11-16T12:00:00Z","sections":[]}
        ]}
        """#
        let pack = try JSONDecoder().decode(DeepDivePack.self, from: Data(json.utf8))
        let topic = Topic(id: "fictional-topic", label: "Fictional topic", scope: "Prototype", description: "Invented")
        let content = BriefingContent(topics: [topic], editions: [], deepDives: pack.deepDives, sources: [])
        XCTAssertEqual(content.currentDeepDives.compactMap(\.revisionID), ["r2"])
        XCTAssertNil(content.currentDeepDive(id: "withdrawn-status"))
        XCTAssertNil(content.currentDeepDive(id: "withdrawn-flag"))
        XCTAssertNil(content.currentDeepDive(id: "superseded-link"))
        XCTAssertNil(content.currentDeepDive(id: "withdrawn-date"))

        let oldTrack = AudioTrack(id: "fictional-dive", title: "Old recording", kind: .explanation,
                                  revision: "r1", scriptHash: "old-hash", resourceFilename: "old.wav")
        guard case .explanation(let resolvedTopic, let diveID)? = TranscriptTarget.resolve(track: oldTrack, in: content) else {
            return XCTFail("Expected the current explanation despite the reversed pack order")
        }
        XCTAssertEqual(resolvedTopic.id, topic.id)
        XCTAssertEqual(diveID, "fictional-dive")

        let ambiguous = BriefingContent(topics: [topic], editions: [],
                                        deepDives: [pack.deepDives[0], DeepDive(
                                            id: "fictional-dive", topicID: topic.id, title: "Ambiguous copy",
                                            reviewedAt: nil, revisionID: "r3", scriptHash: "third-hash",
                                            durationMinutes: nil, summary: nil, sections: [], audio: nil,
                                            superseded: nil, status: nil, withdrawn: nil,
                                            supersededBy: nil, withdrawnAt: nil)], sources: [])
        XCTAssertNil(ambiguous.currentDeepDive(id: "fictional-dive"))
        XCTAssertNil(TranscriptTarget.resolve(track: oldTrack, in: ambiguous),
                     "Ambiguous current explanations must fail closed")
    }

    func testEditionTranscriptFailsClosedForAmbiguousOrWithdrawnCurrentRevision() throws {
        let ambiguousJSON = #"""
        {"editions":[
          {"id":"fictional-edition","kind":"weekly","revision_id":"r2","title":"New copy","sections":[]},
          {"id":"fictional-edition","kind":"weekly","revision_id":"r1","title":"Old copy","sections":[]}
        ]}
        """#
        let ambiguousPack = try JSONDecoder().decode(EditionPack.self, from: Data(ambiguousJSON.utf8))
        let ambiguous = BriefingContent(topics: [], editions: ambiguousPack.editions, deepDives: [], sources: [])
        let track = AudioTrack(id: "fictional-edition", title: "Recording", kind: .weeklyEdition,
                               revision: "r1", scriptHash: "old-hash", resourceFilename: "old.wav")
        XCTAssertNil(ambiguous.currentEdition(id: track.id))
        XCTAssertNil(TranscriptTarget.resolve(track: track, in: ambiguous))

        let withdrawnJSON = #"""
        {"editions":[{"id":"fictional-withdrawn","kind":"weekly","revision_id":"r1",
          "status":"withdrawn","title":"Withdrawn recap","sections":[]}]}
        """#
        let withdrawnPack = try JSONDecoder().decode(EditionPack.self, from: Data(withdrawnJSON.utf8))
        let withdrawn = BriefingContent(topics: [], editions: withdrawnPack.editions, deepDives: [], sources: [])
        let withdrawnTrack = AudioTrack(id: "fictional-withdrawn", title: "Recording", kind: .weeklyEdition,
                                        revision: "r1", scriptHash: "old-hash", resourceFilename: "old.wav")
        XCTAssertNil(TranscriptTarget.resolve(track: withdrawnTrack, in: withdrawn))
    }

    func testPlaybackActionDoesNotBorrowStateFromAnOlderIdentity() {
        let old = AudioTrack(id: "fictional-same-item", title: "Old", kind: .dailyEdition,
                             revision: "same-revision", scriptHash: "old-hash", resourceFilename: "old.wav")
        let corrected = AudioTrack(id: old.id, title: "Current", kind: .dailyEdition,
                                   revision: old.revision, scriptHash: "new-hash", resourceFilename: "new.wav")
        var state = AudioPlaybackState(track: old)
        state.status = .playing
        XCTAssertEqual(playbackActionTitle(track: corrected, state: state, noun: "briefing"), "Play briefing")
        XCTAssertEqual(playbackActionTitle(track: old, state: state, noun: "briefing"), "Pause briefing")
    }

    func testDifferentTranscriptItemMustPushAboveTheVisibleWeeklyReader() {
        let weekly = AudioContentIdentity(kind: .weeklyEdition, id: "fictional-weekly",
                                          revision: "weekly-r2", scriptHash: "weekly-hash")
        let explanation = AudioContentIdentity(kind: .explanation, id: "fictional-sports-dive",
                                               revision: "dive-r2", scriptHash: "dive-hash")
        XCTAssertFalse(transcriptDestinationIsAlreadyVisible(current: weekly, destination: explanation))
        XCTAssertTrue(transcriptDestinationIsAlreadyVisible(current: explanation, destination: explanation))
    }

    func testInternalFictionalSourceDecodesWithoutExternalURL() throws {
        let json = """
        {"id":"record-1","title":"Fictional pilot record — demonstration source",
         "publisher":"Prototype fixture","date":"2026-11-16","kind":"Internal fictional evidence",
         "supported_section":"pilot-count","scope":"Invented count for demonstration",
         "availability":"Bundled only; not externally available.",
         "note":"This evidence is invented."}
        """
        let source = try JSONDecoder().decode(Source.self, from: Data(json.utf8))
        XCTAssertNil(source.url)
        XCTAssertEqual(source.publisher, "Prototype fixture")
        XCTAssertEqual(source.supportedSection, "pilot-count")
        XCTAssertEqual(source.availability, "Bundled only; not externally available.")
    }

    func testLegacySaveSurvivesCorrectionAndCanBeRemoved() {
        let itemID = "legacy-correction-test-\(UUID().uuidString)"
        let legacyR1 = "briefing.saved.\(itemID).fictional-r1"
        let legacyOlder = "briefing.saved.\(itemID).fictional-older"
        let stable = LocalItemStorageKey.saved(itemID: itemID)
        let revision = savedRevisionKey(itemID: itemID)
        defer {
            UserDefaults.standard.removeObject(forKey: stable)
            UserDefaults.standard.removeObject(forKey: revision)
            removeLegacySaves(itemID: itemID)
        }

        UserDefaults.standard.set(true, forKey: legacyR1)
        XCTAssertTrue(isSavedItem(itemID: itemID))
        XCTAssertEqual(savedRevision(itemID: itemID), "fictional-r1")

        UserDefaults.standard.set(true, forKey: legacyOlder)
        XCTAssertTrue(isSavedItem(itemID: itemID), "Multiple legacy keys still represent one saved item")
        XCTAssertNil(savedRevision(itemID: itemID), "Ambiguous old revisions must not be guessed")

        UserDefaults.standard.set(true, forKey: stable)
        UserDefaults.standard.set("fictional-r2", forKey: revision)
        XCTAssertTrue(isSavedItem(itemID: itemID))
        XCTAssertEqual(savedRevision(itemID: itemID), "fictional-r2")

        UserDefaults.standard.removeObject(forKey: stable)
        UserDefaults.standard.removeObject(forKey: revision)
        removeLegacySaves(itemID: itemID)
        XCTAssertFalse(isSavedItem(itemID: itemID), "Unsave removes both stable and legacy saves")
    }

    private func decodeAudioAsset(chapters: String) throws -> AudioAsset {
        let json = """
        {"state":"recorded","revision_id":"rev-1","script_hash":"script-hash",
         "duration_seconds":20,"resource_filename":"fictional.wav","chapters":\(chapters)}
        """
        return try JSONDecoder().decode(AudioAsset.self, from: Data(json.utf8))
    }
}
