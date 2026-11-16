import SwiftUI
import UIKit

@main
struct BriefingApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

@MainActor
struct RootView: View {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var audioController = AudioController()
    @State private var content: BriefingContent?
    @State private var loadError: String?
    @State private var selectedTab = 0
    @State private var todayPath: [ReaderRoute] = []
    @State private var topicsPath: [ReaderRoute] = []
    @State private var libraryPath: [ReaderRoute] = []
    @State private var selectedTopic: Topic?
    @State private var selectedTopicFromToday = false
    @State private var selectedEdition: Edition?
    @State private var contentSheetOriginReader: AudioContentIdentity?
    @State private var settingsOriginReader: AudioContentIdentity?
    @State private var transcriptUnavailable = false
    @State private var transcriptCoverageOriginReader: AudioContentIdentity?
    @State private var transcriptChildRoute: TranscriptChildRoute?
    @State private var lastTranscriptChildRoute: TranscriptChildRoute?
    @State private var pendingTranscriptPushDestination: TranscriptDestination?
    @State private var transcriptNavigationPath: [String] = []
    @State private var transcriptNavigationDestinations: [String: TranscriptDestination] = [:]
    @State private var visibleTranscriptDestination: TranscriptDestination?
    @State private var transcriptMiniFocusToken = 0
    @State private var isSettingsPresented = false
    @State private var tabAudioRoute: AudioModalRoute?
    @State private var pendingTabAudioRoute: AudioModalRoute?
    @State private var lastTabAudioRoute: AudioModalRoute?
    @State private var contentAudioRoute: AudioModalRoute?
    @State private var pendingContentAudioRoute: AudioModalRoute?
    @State private var lastContentAudioRoute: AudioModalRoute?
    @State private var visibleReader: AudioContentIdentity?
    @State private var tabAudioFocusToken = 0
    @State private var contentAudioFocusToken = 0
    #if DEBUG
    @State private var correctionPreviewRevision: String?
    #endif

    var body: some View {
        Group {
            if let content {
                TabView(selection: $selectedTab) {
                    NavigationStack(path: $todayPath) {
                        TodayFrontPage(content: content,
                                       onOpenEdition: { edition, sectionID in
                                           todayPath.append(.edition(edition.id, sectionID))
                                       },
                                       onOpenWeekly: { edition in
                                           todayPath.append(.edition(edition.id, nil))
                                       }, audioState: audioController.state, onPlay: play)
                            .safeAreaInset(edge: .bottom, spacing: 0) {
                                AudioMiniPlayer(controller: audioController, isExpanded: tabPlayerBinding, focusRestoreToken: tabAudioFocusToken, shouldRestoreFocus: selectedTab == 0)
                            }
                            .toolbar {
                                ToolbarItem(placement: .topBarTrailing) {
                                    Button { isSettingsPresented = true } label: {
                                        Image(systemName: "gearshape")
                                            .frame(minWidth: 44, minHeight: 44)
                                    }
                                    .accessibilityLabel("Settings")
                                }
                                #if DEBUG
                                if correctionPreviewRevision == "r1" {
                                    ToolbarItem(placement: .topBarLeading) {
                                        Button("Accept r2 correction") { loadCorrectionPreview("r2") }
                                            .font(.footnote.weight(.medium))
                                            .accessibilityHint("Loads the second fictional correction fixture")
                                    }
                                }
                                #endif
                            }
                            .navigationDestination(for: ReaderRoute.self) { route in
                                readerDestination(route, content: content, root: 0)
                            }
                    }
                    .tabItem { Label("Today", systemImage: "sun.max") }
                    .tag(0)

                    NavigationStack(path: $topicsPath) {
                        TopicsView(content: content, selectedTopic: $selectedTopic,
                                   onReaderVisible: updateVisibleReader,
                                   onOpenTopic: { topicsPath.append(.explanation($0.id)) },
                                   onManageTopics: { isSettingsPresented = true })
                            .safeAreaInset(edge: .bottom, spacing: 0) {
                                AudioMiniPlayer(controller: audioController, isExpanded: tabPlayerBinding, focusRestoreToken: tabAudioFocusToken, shouldRestoreFocus: selectedTab == 1)
                            }
                            .navigationDestination(for: ReaderRoute.self) { route in
                                readerDestination(route, content: content, root: 1)
                            }
                    }
                    .tabItem { Label("Topics", systemImage: "square.grid.2x2") }
                    .tag(1)

                    NavigationStack(path: $libraryPath) {
                        LibraryView(content: content, audioState: audioController.state, onPlay: play,
                                    onReaderVisible: updateVisibleReader,
                                    onOpenEdition: { libraryPath.append(.edition($0.id, nil)) },
                                    onOpenTopic: { libraryPath.append(.explanation($0.id)) },
                                    onSettings: { isSettingsPresented = true })
                            .safeAreaInset(edge: .bottom, spacing: 0) {
                                AudioMiniPlayer(controller: audioController, isExpanded: tabPlayerBinding, focusRestoreToken: tabAudioFocusToken, shouldRestoreFocus: selectedTab == 2)
                            }
                            .navigationDestination(for: ReaderRoute.self) { route in
                                readerDestination(route, content: content, root: 2)
                            }
                    }
                    .tabItem { Label("Library", systemImage: "books.vertical") }
                    .tag(2)
                }
                .tint(ReaderColor.action)
                .sheet(item: $selectedTopic, onDismiss: {
                    selectedTopicFromToday = false
                    restoreContentSheetOrigin()
                }) { topic in
                    NavigationStack { TopicDetailView(topic: topic, content: content, onPlay: play,
                                                      audioState: audioController.state,
                                                      onReturnToToday: selectedTopicFromToday ? {
                                                          selectedTopic = nil
                                                      } : nil,
                                                      onReaderVisible: updateVisibleReader) }
                        .safeAreaInset(edge: .bottom, spacing: 0) {
                            AudioMiniPlayer(controller: audioController, isExpanded: contentPlayerBinding, focusRestoreToken: contentAudioFocusToken, shouldRestoreFocus: selectedTopic != nil)
                        }
                        .sheet(item: $contentAudioRoute, onDismiss: handleContentAudioDismiss) { route in
                            audioRouteView(route, content: content, scope: .content)
                        }
                }
                .sheet(item: $selectedEdition, onDismiss: restoreContentSheetOrigin) { edition in
                    NavigationStack { CoverageView(edition: edition, content: content,
                                                   audioState: audioController.state, onPlay: play,
                                                   onReaderVisible: updateVisibleReader) }
                        .safeAreaInset(edge: .bottom, spacing: 0) {
                            AudioMiniPlayer(controller: audioController, isExpanded: contentPlayerBinding, focusRestoreToken: contentAudioFocusToken, shouldRestoreFocus: selectedEdition != nil)
                        }
                        .sheet(item: $contentAudioRoute, onDismiss: handleContentAudioDismiss) { route in
                            audioRouteView(route, content: content, scope: .content)
                        }
                }
                .sheet(item: $tabAudioRoute, onDismiss: handleTabAudioDismiss) { route in
                    audioRouteView(route, content: content, scope: .tab)
                }
                .sheet(isPresented: $isSettingsPresented, onDismiss: restoreSettingsOrigin) {
                    PreferencesView(topics: content.topics)
                        .onAppear { settingsOriginReader = visibleReader; visibleReader = nil }
                        .safeAreaInset(edge: .bottom, spacing: 0) {
                            AudioMiniPlayer(controller: audioController, isExpanded: contentPlayerBinding, focusRestoreToken: contentAudioFocusToken, shouldRestoreFocus: isSettingsPresented)
                        }
                        .sheet(item: $contentAudioRoute, onDismiss: handleContentAudioDismiss) { route in
                            audioRouteView(route, content: content, scope: .content)
                        }
                }
            } else {
                ContentUnavailableView("Briefing unavailable", systemImage: "doc.text", description: Text(loadError ?? "Loading bundled content…"))
            }
        }
        .task { loadContent() }
        .onChange(of: selectedTopic?.id) { oldValue, newValue in
            if oldValue == nil, newValue != nil { contentSheetOriginReader = visibleReader }
        }
        .onChange(of: selectedEdition?.id) { oldValue, newValue in
            if oldValue == nil, newValue != nil { contentSheetOriginReader = visibleReader }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { audioController.appWillResignActive() }
        }
        .preferredColorScheme(nil)
    }

    @ViewBuilder
    private func readerDestination(_ route: ReaderRoute, content: BriefingContent, root: Int) -> some View {
        switch route {
        case .edition(let id, let sectionID):
            if let edition = content.currentEditions.first(where: { $0.id == id }) {
                EditionReaderView(edition: edition, content: content,
                                  navigationTitle: edition.kind == "weekly" ? "Recap" : "Briefing",
                                  audioState: audioController.state, onPlay: play,
                                  onCoverage: { _ in },
                                  onReaderVisible: updateVisibleReader,
                                  onExploreTopic: { push(.explanation($0.id), in: root) },
                                  onOpenSource: { push(.source($0.id), in: root) },
                                  onOpenWeekly: { push(.edition($0.id, nil), in: root) },
                                  initialSectionID: sectionID,
                                  onOpenNextStory: { nextID in push(.edition(id, nextID), in: root) })
                    .toolbar(.hidden, for: .tabBar)
            } else {
                ContentUnavailableView("Edition unavailable", systemImage: "text.page",
                                       description: Text("This edition is not in the sample pack."))
            }
        case .explanation(let topicID):
            if let topic = content.topics.first(where: { $0.id == topicID }) {
                TopicDetailView(topic: topic, content: content, onPlay: play,
                                audioState: audioController.state,
                                onReaderVisible: updateVisibleReader,
                                usesNavigationBackButton: true,
                                onOpenSource: { push(.source($0.id), in: root) })
                    .toolbar(.hidden, for: .tabBar)
            } else {
                ContentUnavailableView("Explanation unavailable", systemImage: "text.page",
                                       description: Text("This topic is not in the sample pack."))
            }
        case .source(let sourceID):
            if let source = content.sources.first(where: { $0.id == sourceID }) {
                SourceDetailView(source: source)
                    .toolbar(.hidden, for: .tabBar)
            } else {
                ContentUnavailableView("Source unavailable", systemImage: "doc.text",
                                       description: Text("This source is not in the sample pack."))
            }
        }
    }

    private func push(_ route: ReaderRoute, in root: Int) {
        switch root {
        case 1: topicsPath.append(route)
        case 2: libraryPath.append(route)
        default: todayPath.append(route)
        }
    }

    private func loadContent() {
        do {
            #if DEBUG
            let previewRevision = correctionPreviewArgument
            let loaded: BriefingContent
            if let previewRevision {
                let directory = correctionFixtureDirectory(previewRevision)
                loaded = try BriefingContent.load(directory: directory)
                correctionPreviewRevision = previewRevision
            } else {
                loaded = try BriefingContent.load()
            }
            #else
            let loaded = try BriefingContent.load()
            #endif
            content = loaded
            #if DEBUG
            if previewRevision == "r1", CommandLine.arguments.contains("--briefing-correction-seed-save"),
               let edition = loaded.currentEdition(kind: "daily") {
                UserDefaults.standard.set(true, forKey: LocalItemStorageKey.saved(itemID: edition.id))
                UserDefaults.standard.set(edition.revisionID, forKey: "briefing.saved.revision.\(edition.id)")
                UserDefaults.standard.set(true, forKey: ReaderProgressKey.finished(itemID: edition.id, revisionID: edition.revisionID ?? "unversioned"))
            }
            if CommandLine.arguments.contains("--briefing-correction-library") { selectedTab = 2 }
            if loadPlaybackFixtureIfRequested(loaded) { return }
            if let edition = loaded.currentEdition(kind: "daily"),
               !CommandLine.arguments.contains("--briefing-correction-library") {
                if CommandLine.arguments.contains("--briefing-correction-source"),
                   let sourceID = edition.materialCorrection?.sourceIDs.first ?? edition.sections.first?.sourceIDs?.first {
                    todayPath = [.edition(edition.id, nil), .source(sourceID)]
                } else if CommandLine.arguments.contains("--briefing-deeper-preview") ||
                          CommandLine.arguments.contains("--briefing-source-preview"),
                          let topicID = edition.sections.first?.topicIDs?.first {
                    todayPath = [.edition(edition.id, nil), .explanation(topicID)]
                    if CommandLine.arguments.contains("--briefing-source-preview"),
                       let dive = loaded.currentDeepDive(forTopic: topicID),
                       let sourceID = dive.sections.first?.sourceIDs?.first {
                        todayPath.append(.source(sourceID))
                    }
                } else if CommandLine.arguments.contains("--briefing-reader-preview") || previewRevision != nil {
                    todayPath = [.edition(edition.id, nil)]
                }
            }
            #endif
            let editionTracks = loaded.currentEditions.compactMap {
                $0.audio?.track(id: $0.id, title: $0.title,
                                kind: $0.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                                revisionID: $0.revisionID, scriptHash: $0.scriptHash)
            }
            let deepDiveTracks = loaded.currentDeepDives.compactMap {
                $0.audio?.track(id: $0.id, title: $0.title,
                                kind: .explanation,
                                revisionID: $0.revisionID, scriptHash: $0.scriptHash)
            }
            audioController.restoreLastSelectedTrack(from: editionTracks + deepDiveTracks)
        }
        catch { loadError = error.localizedDescription }
    }

    #if DEBUG
    private var correctionPreviewArgument: String? {
        if CommandLine.arguments.contains("--briefing-correction-preview-r1") { return "r1" }
        if CommandLine.arguments.contains("--briefing-correction-preview-r2") { return "r2" }
        return nil
    }

    private func correctionFixtureDirectory(_ revision: String) -> URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("briefing-correction", isDirectory: true)
            .appendingPathComponent(revision, isDirectory: true)
    }

    private func loadCorrectionPreview(_ revision: String) {
        do {
            let loaded = try BriefingContent.load(directory: correctionFixtureDirectory(revision))
            if let oldEdition = content?.currentEdition(kind: "daily"),
               let activeTrack = audioController.state.track,
               activeTrack.id == oldEdition.id,
               let currentEdition = loaded.currentEdition(kind: "daily"),
               activeTrack.contentIdentity != AudioContentIdentity(
                    kind: .dailyEdition, id: currentEdition.id,
                    revision: currentEdition.revisionID ?? "unversioned",
                    scriptHash: currentEdition.scriptHash ?? "") {
                audioController.invalidateCurrentTrack()
            }
            content = loaded
            correctionPreviewRevision = revision
            let editionTracks = loaded.currentEditions.compactMap {
                $0.audio?.track(id: $0.id, title: $0.title,
                                kind: $0.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                                revisionID: $0.revisionID, scriptHash: $0.scriptHash)
            }
            let deepDiveTracks = loaded.currentDeepDives.compactMap {
                $0.audio?.track(id: $0.id, title: $0.title,
                                kind: .explanation,
                                revisionID: $0.revisionID, scriptHash: $0.scriptHash)
            }
            audioController.restoreLastSelectedTrack(from: editionTracks + deepDiveTracks)
        } catch { loadError = error.localizedDescription }
    }
    #endif

    #if DEBUG
    private func loadPlaybackFixtureIfRequested(_ loaded: BriefingContent) -> Bool {
        let fixtureURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)
            .first?.appendingPathComponent("playback-fixture.wav")
        let supersededFixture = CommandLine.arguments.contains("--briefing-superseded-fixture")
        guard CommandLine.arguments.contains("--briefing-playback-fixture") || supersededFixture,
              let url = fixtureURL, FileManager.default.fileExists(atPath: url.path) else {
            guard transcriptPreviewMode != nil, let url = fixtureURL,
                  FileManager.default.fileExists(atPath: url.path) else { return false }
            return loadTranscriptPreviewFixture(content: loaded, url: url)
        }
        if supersededFixture {
            let oldTrack = AudioTrack(id: "fictional-correction-edition", title: "A fictional checkout pilot",
                                      kind: .dailyEdition, revision: "fictional-r1", scriptHash: "fictional-old-hash",
                                      resourceFilename: "playback-fixture.wav")
            audioController.restore(oldTrack, from: url)
            audioController.invalidateCurrentTrack()
            if let edition = loaded.currentEdition(kind: "daily") {
                todayPath = [.edition(edition.id, nil)]
            }
            return true
        }
        let middleTitle = CommandLine.arguments.contains("--briefing-long-chapter-fixture")
            ? "How a faster checkout changes the choices people can make"
            : "Why it matters"
        let chapters: [AudioChapter] = CommandLine.arguments.contains("--briefing-chapters-fixture") ? [
            AudioChapter(id: "opening", title: "What changed", startSeconds: 0),
            AudioChapter(id: "impact", title: middleTitle, startSeconds: 5),
            AudioChapter(id: "next", title: "What to watch", startSeconds: 12)
        ] : []
        let track = AudioTrack(id: "simulator-fixture", title: "Playback test recording",
                               kind: .fixture, revision: "fixture-v1", scriptHash: "fixture-script-hash",
                               resourceFilename: "playback-fixture.wav",
                               chapters: chapters)
        if CommandLine.arguments.contains("--briefing-seek-six") {
            UserDefaults.standard.set(6.0, forKey: "briefing.audio.fixture.simulator-fixture.fixture-v1.fixture-script-hash")
        }
        audioController.restore(track, from: url)
        if CommandLine.arguments.contains("--briefing-audio-expanded") { presentAudioPlayer(in: .tab) }
        return true
    }

    private var transcriptPreviewMode: String? {
        let modes = ["daily", "older-daily", "weekly", "explanation", "topic-origin",
                     "coverage-origin", "library", "missing", "stale-explanation"]
        return modes.first(where: { CommandLine.arguments.contains("--briefing-transcript-\($0)") })
    }

    private func transcriptPreviewTrack(mode: String, content: BriefingContent) -> AudioTrack? {
        let edition: Edition?
        switch mode {
        case "daily", "older-daily", "topic-origin", "coverage-origin", "library":
            edition = mode == "topic-origin" ? content.currentEdition(kind: "weekly") : content.currentEdition(kind: "daily")
        case "weekly": edition = content.currentEdition(kind: "weekly")
        default: edition = nil
        }
        if (mode == "explanation" || mode == "stale-explanation"), let dive = content.currentDeepDives.last {
            let stale = mode == "stale-explanation"
            return AudioTrack(id: dive.id, title: dive.title, kind: .explanation,
                              revision: (dive.revisionID ?? "debug-r1") + (stale ? "-old" : ""),
                              scriptHash: (dive.scriptHash ?? "debug-hash") + (stale ? "-old" : ""),
                              resourceFilename: "playback-fixture.wav")
        }
        if mode == "missing" {
            return AudioTrack(id: "fictional-item-not-in-pack", title: "Missing transcript fixture",
                              kind: .weeklyEdition, revision: "debug-r1", scriptHash: "debug-hash",
                              resourceFilename: "playback-fixture.wav")
        }
        if mode == "older-daily" {
            return AudioTrack(id: "fictional-older-daily-flow", title: "Older daily fixture",
                              kind: .dailyEdition, revision: "older-flow-r1", scriptHash: "older-flow-hash",
                              resourceFilename: "playback-fixture.wav")
        }
        guard let edition else { return nil }
        return AudioTrack(id: edition.id, title: edition.title,
                          kind: edition.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                          revision: edition.revisionID ?? "debug-r1",
                          scriptHash: edition.scriptHash ?? "debug-hash",
                          resourceFilename: "playback-fixture.wav")
    }

    private func olderDailyPreviewContent(from loaded: BriefingContent) -> BriefingContent {
        guard let current = loaded.currentEdition(kind: "daily") else { return loaded }
        let older = Edition(id: "fictional-older-daily-flow", kind: "daily",
                            localDate: "2026-11-14", weekStart: nil,
                            revisionID: "older-flow-r1", scriptHash: "older-flow-hash",
                            title: "Older fictional daily briefing", asOf: nil,
                            durationMinutes: current.durationMinutes, summary: current.summary,
                            sections: current.sections, coverage: current.coverage,
                            sourceIDs: current.sourceIDs, audio: nil, superseded: nil,
                            status: nil, withdrawn: nil, supersededBy: nil, withdrawnAt: nil, corrections: nil)
        return BriefingContent(topics: loaded.topics, editions: [older] + loaded.editions,
                               deepDives: loaded.deepDives, sources: loaded.sources)
    }

    private func loadTranscriptPreviewFixture(content loaded: BriefingContent, url: URL) -> Bool {
        guard let mode = transcriptPreviewMode,
              let track = transcriptPreviewTrack(mode: mode, content: loaded) else { return false }
        if mode == "older-daily" {
            let olderContent = olderDailyPreviewContent(from: loaded)
            content = olderContent
            if let olderEdition = olderContent.currentEditions.first(where: { $0.id == track.id }) {
                todayPath = [.edition(olderEdition.id, olderEdition.readingSections.first?.id)]
            }
            selectedTab = 0
        } else if mode == "topic-origin" {
            guard let dive = loaded.currentDeepDives.last,
                  let topic = loaded.topics.first(where: { $0.id == dive.topicID }) else { return false }
            selectedTopic = topic
            selectedTab = 0
        } else if mode == "coverage-origin" {
            guard let edition = loaded.currentEdition(kind: "daily") else { return false }
            selectedEdition = edition
            selectedTab = 0
        } else if mode == "library" {
            selectedTab = 2
        }
        audioController.restore(track, from: url)
        if mode == "stale-explanation" { audioController.invalidateCurrentTrack() }
        if mode == "topic-origin" || mode == "coverage-origin" {
            presentAudioPlayer(in: .content)
        } else {
            presentAudioPlayer(in: .tab)
        }
        return true
    }
    #endif

    private func play(_ track: AudioTrack) {
        audioController.select(track)
    }

    private var tabPlayerBinding: Binding<Bool> {
        Binding(get: {
            guard let tabAudioRoute else { return false }
            if case .player(returnTo: _, returnReader: _) = tabAudioRoute { return true }
            return false
        },
                set: { if $0 { presentAudioPlayer(in: .tab) } })
    }

    private var contentPlayerBinding: Binding<Bool> {
        Binding(get: {
            guard let contentAudioRoute else { return false }
            if case .player(returnTo: _, returnReader: _) = contentAudioRoute { return true }
            return false
        },
                set: { if $0 { presentAudioPlayer(in: .content) } })
    }

    private var transcriptPlayerBinding: Binding<Bool> {
        Binding(get: {
            guard let transcriptChildRoute else { return false }
            if case .player = transcriptChildRoute { return true }
            return false
        }, set: { if $0 { presentTranscriptPlayer() } })
    }

    private func route(in scope: AudioSheetScope) -> AudioModalRoute? {
        scope == .tab ? tabAudioRoute : contentAudioRoute
    }

    private func setRoute(_ route: AudioModalRoute?, in scope: AudioSheetScope) {
        if scope == .tab { tabAudioRoute = route } else { contentAudioRoute = route }
    }

    private func setPendingRoute(_ route: AudioModalRoute?, in scope: AudioSheetScope) {
        if scope == .tab { pendingTabAudioRoute = route } else { pendingContentAudioRoute = route }
    }

    private func lastRoute(in scope: AudioSheetScope) -> AudioModalRoute? {
        scope == .tab ? lastTabAudioRoute : lastContentAudioRoute
    }

    private func setLastRoute(_ route: AudioModalRoute?, in scope: AudioSheetScope) {
        if scope == .tab { lastTabAudioRoute = route } else { lastContentAudioRoute = route }
    }

    private func transitionAudio(to next: AudioModalRoute?, in scope: AudioSheetScope) {
        guard let current = route(in: scope) else {
            setPendingRoute(nil, in: scope)
            setRoute(next, in: scope)
            setLastRoute(next, in: scope)
            return
        }
        setPendingRoute(next, in: scope)
        setLastRoute(current, in: scope)
        setRoute(nil, in: scope)
    }

    private func handleTabAudioDismiss() { handleAudioDismiss(in: .tab) }
    private func handleContentAudioDismiss() { handleAudioDismiss(in: .content) }

    private func handleAudioDismiss(in scope: AudioSheetScope) {
        transcriptNavigationPath = []
        transcriptNavigationDestinations = [:]
        visibleTranscriptDestination = nil
        let pending = scope == .tab ? pendingTabAudioRoute : pendingContentAudioRoute
        if let pending {
            setPendingRoute(nil, in: scope)
            setRoute(pending, in: scope)
            setLastRoute(pending, in: scope)
            return
        }
        if let returnTo = lastRoute(in: scope)?.returnTo {
            setRoute(returnTo, in: scope)
            setLastRoute(returnTo, in: scope)
            return
        }
        if let returnReader = lastRoute(in: scope)?.returnReader { visibleReader = returnReader }
        setLastRoute(nil, in: scope)
        if scope == .tab { tabAudioFocusToken &+= 1 }
        else { contentAudioFocusToken &+= 1 }
    }

    private func presentAudioPlayer(in scope: AudioSheetScope) {
        if let current = route(in: scope),
           case .player(returnTo: _, returnReader: _) = current { return }
        transitionAudio(to: .player(returnTo: route(in: scope), returnReader: visibleReader), in: scope)
    }

    private func openTranscript(for track: AudioTrack?, content: BriefingContent,
                                scope: AudioSheetScope, playerReturnTo: AudioModalRoute?,
                                playerReturnReader: AudioContentIdentity?) {
        guard let track, let destination = transcriptDestination(for: track, content: content) else {
            transcriptUnavailable = true
            return
        }
        let destinationIdentity = identity(for: destination, content: content)
        if track.contentIdentity != destinationIdentity,
           audioController.state.track?.contentIdentity == track.contentIdentity {
            audioController.invalidateCurrentTrack()
        }
        if canDismissTranscriptWithoutNavigation(track: track, destination: destinationIdentity,
                                                 visibleReader: visibleReader, originReader: playerReturnReader) {
            transitionAudio(to: nil, in: scope)
            return
        }
        transitionAudio(to: .transcript(destination, returnTo: playerReturnTo,
                                        returnReader: playerReturnReader), in: scope)
    }

    private func openTranscriptFromVisibleTranscript(for track: AudioTrack?,
                                                     currentDestination: TranscriptDestination,
                                                     content: BriefingContent) {
        let currentIdentity = identity(for: currentDestination, content: content)
        guard let track,
              let destination = transcriptDestination(for: track, content: content,
                                                       returnReader: currentIdentity,
                                                       returnTitleOverride: "Back to player") else {
            transcriptUnavailable = true
            return
        }
        let destinationIdentity = identity(for: destination, content: content)
        if track.contentIdentity != destinationIdentity,
           audioController.state.track?.contentIdentity == track.contentIdentity {
            audioController.invalidateCurrentTrack()
        }
        if destinationIdentity == currentIdentity {
            transcriptChildRoute = nil
            return
        }

        pendingTranscriptPushDestination = destination
        transcriptChildRoute = nil
    }

    private func transcriptDestination(for track: AudioTrack, content: BriefingContent,
                                       returnReader: AudioContentIdentity? = nil,
                                       returnTitleOverride: String? = nil) -> TranscriptDestination? {
        let returnTitle = returnTitleOverride ?? transcriptReturnTitle(for: returnReader ?? visibleReader, in: content)
        switch TranscriptTarget.resolve(track: track, in: content) {
        case .edition(let edition): return .edition(edition, returnTitle: returnTitle)
        case .explanation(let topic, let diveID):
            return .topic(topic, diveID: diveID, returnTitle: returnTitle)
        case nil: return nil
        }
    }

    private func identity(for destination: TranscriptDestination, content: BriefingContent) -> AudioContentIdentity {
        switch destination {
        case .edition(let edition, _):
            return AudioContentIdentity(kind: edition.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                                        id: edition.id, revision: edition.revisionID ?? "unversioned",
                                        scriptHash: edition.scriptHash ?? "")
        case .topic(_, let diveID, _):
            let dive = content.currentDeepDive(id: diveID)
            return AudioContentIdentity(kind: .explanation, id: diveID,
                                        revision: dive?.revisionID ?? "unversioned",
                                        scriptHash: dive?.scriptHash ?? "")
        }
    }

    private func transcriptReturnTitle(for identity: AudioContentIdentity?, in content: BriefingContent) -> String {
        if let identity, identity.kind == .explanation,
           let dive = content.currentDeepDive(id: identity.id),
           let topic = content.topics.first(where: { $0.id == dive.topicID }) {
            return "Back to \(topic.label)"
        }
        if let selectedTopic { return "Back to \(selectedTopic.label)" }
        if selectedEdition != nil { return "Back to Coverage" }
        if isSettingsPresented { return "Back to Settings" }
        switch selectedTab {
        case 2: return "Back to Library"
        case 1: return "Back to Topics"
        default: return "Back to Today"
        }
    }

    private func updateVisibleReader(_ identity: AudioContentIdentity?) {
        visibleReader = identity
    }

    private func restoreContentSheetOrigin() {
        visibleReader = contentSheetOriginReader
        contentSheetOriginReader = nil
    }

    private func restoreSettingsOrigin() {
        visibleReader = settingsOriginReader
        settingsOriginReader = nil
    }

    private func restoreTranscriptCoverageOrigin() {
        visibleReader = transcriptCoverageOriginReader
        transcriptCoverageOriginReader = nil
    }

    private func presentTranscriptPlayer() {
        guard case nil = transcriptChildRoute else { return }
        lastTranscriptChildRoute = .player
        transcriptChildRoute = .player
    }

    private func presentTranscriptCoverage(_ edition: Edition, origin: AudioContentIdentity?) {
        guard case nil = transcriptChildRoute else { return }
        transcriptCoverageOriginReader = origin
        lastTranscriptChildRoute = .coverage(edition)
        transcriptChildRoute = .coverage(edition)
    }

    private func handleTranscriptChildDismiss() {
        if let pending = pendingTranscriptPushDestination {
            pendingTranscriptPushDestination = nil
            lastTranscriptChildRoute = nil
            let navigationID = UUID().uuidString
            transcriptNavigationDestinations[navigationID] = pending
            transcriptNavigationPath.append(navigationID)
            return
        }
        switch lastTranscriptChildRoute {
        case .some(.player):
            transcriptMiniFocusToken &+= 1
        case .some(.coverage(_)):
            restoreTranscriptCoverageOrigin()
        case .none:
            break
        }
        lastTranscriptChildRoute = nil
    }

    @ViewBuilder
    private func audioRouteView(_ route: AudioModalRoute, content: BriefingContent,
                                scope: AudioSheetScope) -> some View {
        switch route {
        case .player(let returnTo, let returnReader):
            AudioExpandedPlayer(controller: audioController,
                                transcriptTitle: transcriptButtonTitle(for: audioController.state.track, in: content)) {
                openTranscript(for: audioController.state.track, content: content,
                               scope: scope, playerReturnTo: returnTo,
                               playerReturnReader: returnReader)
            }
            .alert("Transcript unavailable", isPresented: $transcriptUnavailable) {
                Button("OK", role: .cancel) { }
            } message: {
                Text("This transcript is unavailable.")
            }
        case .transcript(let destination, let returnTo, _):
            NavigationStack(path: $transcriptNavigationPath) {
                transcriptReaderContent(destination, content: content,
                                        returnAction: { transitionAudio(to: returnTo, in: scope) })
                    .onAppear { visibleTranscriptDestination = destination }
                    .navigationDestination(for: String.self) { navigationID in
                        if let pushed = transcriptNavigationDestinations[navigationID] {
                            transcriptReaderContent(pushed, content: content,
                                                    returnAction: nil, usesNavigationBackButton: true)
                                .onAppear { visibleTranscriptDestination = pushed }
                        }
                    }
            }
            .onChange(of: transcriptNavigationPath) { oldPath, path in
                visibleTranscriptDestination = path.last.flatMap { transcriptNavigationDestinations[$0] } ?? destination
                if oldPath.count > path.count {
                    DispatchQueue.main.async { transcriptMiniFocusToken &+= 1 }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                AudioMiniPlayer(controller: audioController, isExpanded: transcriptPlayerBinding,
                                focusRestoreToken: transcriptMiniFocusToken)
            }
            .sheet(item: $transcriptChildRoute, onDismiss: handleTranscriptChildDismiss) { childRoute in
                switch childRoute {
                case .player:
                    AudioExpandedPlayer(controller: audioController,
                                        transcriptTitle: transcriptButtonTitle(for: audioController.state.track, in: content)) {
                        openTranscriptFromVisibleTranscript(for: audioController.state.track,
                                                            currentDestination: visibleTranscriptDestination ?? destination,
                                                            content: content)
                    }
                    .alert("Transcript unavailable", isPresented: $transcriptUnavailable) {
                        Button("OK", role: .cancel) { }
                    } message: {
                        Text("This transcript is unavailable.")
                    }
                case .coverage(let edition):
                    NavigationStack {
                        CoverageView(edition: edition, content: content,
                                     audioState: audioController.state, onPlay: play,
                                     onReaderVisible: updateVisibleReader)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func transcriptReaderContent(_ destination: TranscriptDestination,
                                         content: BriefingContent,
                                         returnAction: (() -> Void)?,
                                         usesNavigationBackButton: Bool = false) -> some View {
        switch destination {
        case .edition(let edition, let returnTitle):
            EditionReaderView(edition: edition, content: content,
                              navigationTitle: edition.kind == "weekly" ? "Weekly" : "Edition",
                              audioState: audioController.state, onPlay: play,
                              onCoverage: { selectedCoverage in
                                  presentTranscriptCoverage(selectedCoverage,
                                                            origin: identity(for: .edition(edition, returnTitle: ""), content: content))
                              },
                              onReaderVisible: updateVisibleReader)
                .toolbar {
                    if let returnAction {
                        ToolbarItem(placement: .topBarLeading) {
                            ContextReturnButton(title: returnTitle, action: returnAction)
                        }
                    }
                }
        case .topic(let topic, let diveID, let returnTitle):
            TopicDetailView(topic: topic, content: content, onPlay: play,
                            audioState: audioController.state, initialDiveID: diveID,
                            transcriptReturnTitle: returnAction == nil ? nil : returnTitle,
                            onTranscriptReturn: returnAction,
                            onReaderVisible: updateVisibleReader,
                            usesNavigationBackButton: usesNavigationBackButton)
        }
    }

    private func transcriptButtonTitle(for track: AudioTrack?, in content: BriefingContent) -> String {
        guard let track else { return "Read transcript" }
        switch TranscriptTarget.resolve(track: track, in: content) {
        case .edition(let edition):
            let current = AudioContentIdentity(kind: edition.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                                                id: edition.id, revision: edition.revisionID ?? "unversioned",
                                                scriptHash: edition.scriptHash ?? "")
            if track.contentIdentity != current, edition.materialCorrection != nil { return "Read corrected edition" }
            return track.contentIdentity == current ? "Read transcript" : "Read current edition"
        case .explanation(_, let diveID):
            guard let dive = content.currentDeepDive(id: diveID) else { return "Read transcript" }
            let current = AudioContentIdentity(kind: .explanation, id: dive.id,
                                               revision: dive.revisionID ?? "unversioned",
                                               scriptHash: dive.scriptHash ?? "")
            return track.contentIdentity == current ? "Read transcript" : "Read current explanation"
        case nil:
            return "Read transcript"
        }
    }

}

enum TranscriptTarget {
    case edition(Edition)
    case explanation(Topic, diveID: String)

    static func resolve(track: AudioTrack, in content: BriefingContent) -> TranscriptTarget? {
        switch track.kind {
        case .dailyEdition, .weeklyEdition:
            let expectedKind = track.kind == .weeklyEdition ? "weekly" : "daily"
            guard let current = content.currentEditions.last(where: { $0.id == track.id && $0.kind == expectedKind }) else { return nil }
            return .edition(current)
        case .explanation:
            guard let currentDive = content.currentDeepDive(id: track.id),
                  content.currentDeepDive(forTopic: currentDive.topicID)?.id == currentDive.id,
                  let topic = content.topics.first(where: { $0.id == currentDive.topicID }) else { return nil }
            return .explanation(topic, diveID: currentDive.id)
        case .fixture:
            return nil
        }
    }
}

func canDismissTranscriptWithoutNavigation(track: AudioTrack, destination: AudioContentIdentity,
                                           visibleReader: AudioContentIdentity?,
                                           originReader: AudioContentIdentity? = nil) -> Bool {
    track.contentIdentity == destination &&
        (visibleReader == destination || originReader == destination)
}

private enum TranscriptDestination: Identifiable {
    case edition(Edition, returnTitle: String)
    case topic(Topic, diveID: String, returnTitle: String)

    var id: String {
        switch self {
        case .edition(let edition, _): return "edition-\(edition.id)-\(edition.revisionID ?? "unversioned")"
        case .topic(let topic, let diveID, _): return "topic-\(topic.id)-\(diveID)"
        }
    }
}

private enum AudioSheetScope: Equatable {
    case tab
    case content
}

private enum TranscriptChildRoute: Identifiable {
    case player
    case coverage(Edition)

    var id: String {
        switch self {
        case .player: return "transcript-player"
        case .coverage(let edition): return "transcript-coverage-\(edition.id)-\(edition.revisionID ?? "unversioned")"
        }
    }
}

private indirect enum AudioModalRoute: Identifiable {
    case player(returnTo: AudioModalRoute?, returnReader: AudioContentIdentity?)
    case transcript(TranscriptDestination, returnTo: AudioModalRoute?, returnReader: AudioContentIdentity?)

    var returnTo: AudioModalRoute? {
        switch self {
        case .player(let route, _), .transcript(_, let route, _): return route
        }
    }

    var returnReader: AudioContentIdentity? {
        switch self {
        case .player(_, let identity), .transcript(_, _, let identity): return identity
        }
    }

    var id: String {
        switch self {
        case .player(returnTo: _, returnReader: _): return "audio-player"
        case .transcript(let destination, _, _): return "transcript-\(destination.id)"
        }
    }
}

enum ReaderColor {
    static let action = Color(light: 0x215DB0, dark: 0x9BC2FF)
    static let primaryAction = Color(light: 0x215DB0, dark: 0x215DB0)
    static let secondary = Color(light: 0x62676F, dark: 0xAEB4BF)
    static let separator = Color(light: 0xE5E5EA, dark: 0x303036)
    static let correction = Color(light: 0x9A3412, dark: 0xFDBA74)
}

private extension Color {
    init(light: UInt, dark: UInt) {
        self.init(uiColor: UIColor { traits in
            let value = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: CGFloat((value >> 16) & 0xff) / 255,
                           green: CGFloat((value >> 8) & 0xff) / 255,
                           blue: CGFloat(value & 0xff) / 255,
                           alpha: 1)
        })
    }
}
