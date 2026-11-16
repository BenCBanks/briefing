import SwiftUI

struct TodayView: View {
    let content: BriefingContent
    let onPlay: (AudioTrack) -> Void
    let onCoverage: (Edition) -> Void
    let onExploreTopic: (Topic) -> Void
    var audioState = AudioPlaybackState()
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    var debugSourceID: String? = nil
    private var daily: Edition? { content.currentEdition(kind: "daily") }

    var body: some View {
        Group {
            if let edition = daily {
                EditionReaderView(edition: edition, content: content, navigationTitle: "Today",
                                  audioState: audioState, onPlay: onPlay, onCoverage: onCoverage,
                                  onReaderVisible: onReaderVisible,
                                  onExploreTopic: onExploreTopic, debugSourceID: debugSourceID)
                    .id("\(edition.id).\(edition.revisionID ?? "unversioned").\(edition.scriptHash ?? "unhashed")")
            } else {
                ContentUnavailableView("No edition available", systemImage: "text.page", description: Text("The bundled sample pack has no daily edition."))
            }
        }
        .navigationTitle("Today")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { if daily == nil { onReaderVisible?(nil) } }
    }
}

struct EditionReaderView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let edition: Edition
    let content: BriefingContent
    let navigationTitle: String
    let audioState: AudioPlaybackState
    let onPlay: (AudioTrack) -> Void
    let onCoverage: (Edition) -> Void
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    var onExploreTopic: ((Topic) -> Void)? = nil
    var debugSourceID: String? = nil
    @State private var isFinished: Bool
    @State private var readingSectionID: String?
    @State private var selectedSource: Source?
    @State private var focusSectionID: String?
    @State private var isRestoringReadingPosition = false
    @State private var didInitializeReading = false
    @AccessibilityFocusState private var articleTitleFocused: Bool
    @AccessibilityFocusState private var correctionHeadingFocused: Bool
    @ScaledMetric(relativeTo: .title) private var titleSize: CGFloat = 26
    @ScaledMetric(relativeTo: .body) private var summarySize: CGFloat = 18

    init(edition: Edition, content: BriefingContent, navigationTitle: String,
         audioState: AudioPlaybackState = AudioPlaybackState(),
         onPlay: @escaping (AudioTrack) -> Void = { _ in },
         onCoverage: @escaping (Edition) -> Void = { _ in },
         onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil,
         onExploreTopic: ((Topic) -> Void)? = nil,
         debugSourceID: String? = nil) {
        self.edition = edition
        self.content = content
        self.navigationTitle = navigationTitle
        self.audioState = audioState
        self.onPlay = onPlay
        self.onCoverage = onCoverage
        self.onReaderVisible = onReaderVisible
        self.onExploreTopic = onExploreTopic
        self.debugSourceID = debugSourceID
        let key = Self.finishedKey(edition)
        _isFinished = State(initialValue: UserDefaults.standard.bool(forKey: key))
        _readingSectionID = State(initialValue: UserDefaults.standard.string(forKey: Self.readingKey(edition)))
    }

    var body: some View {
        ScrollViewReader { scrollProxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("\(edition.localDate ?? edition.weekStart ?? edition.asOf ?? "Edition") · Sample · Fictional content")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)

                    Text(edition.title).font(.system(size: titleSize, weight: .semibold))
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityFocused($articleTitleFocused)
                    Text(track == nil && edition.materialCorrection == nil ? "\(metadata) · Audio pending approval" : metadata)
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)

                    if let correction = edition.materialCorrection {
                        VStack(alignment: .leading, spacing: 10) {
                            Label(correctionHeading(correction), systemImage: "exclamationmark.bubble")
                                .font(.subheadline.weight(.semibold))
                                .accessibilityAddTraits(.isHeader)
                                .accessibilityFocused($correctionHeadingFocused)
                            Text(correction.whatChanged).font(.subheadline)
                            Text(correction.whyItMatters).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                            if track == nil {
                                Text("Updated audio is unavailable for this corrected edition.")
                                    .font(.footnote).foregroundStyle(ReaderColor.secondary)
                            }
                            Button("Read the corrected section") {
                                guard let anchor = correction.sectionIDs.first,
                                      edition.sections.contains(where: { $0.id == anchor }) else { return }
                                if reduceMotion { scrollProxy.scrollTo(anchor, anchor: .top) }
                                else { withAnimation(.easeInOut(duration: 0.25)) { scrollProxy.scrollTo(anchor, anchor: .top) } }
                                focusSectionID = nil
                                DispatchQueue.main.async { focusSectionID = anchor }
                            }
                            .font(.subheadline.weight(.medium)).frame(minHeight: 44, alignment: .leading)
                            ForEach(correction.sourceIDs, id: \.self) { sourceID in
                                if let source = content.sources.first(where: { $0.id == sourceID }) {
                                    Button("Evidence: \(source.title)") { selectedSource = source }
                                        .font(.footnote.weight(.medium)).frame(minHeight: 44, alignment: .leading)
                                }
                            }
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(ReaderColor.correction.opacity(0.10), in: RoundedRectangle(cornerRadius: 14))
                        .accessibilityElement(children: .contain)
                    }

                    if let track {
                        Button(actionTitle, action: { onPlay(track) })
                            .font(.headline)
                            .frame(maxWidth: .infinity, minHeight: 48)
                            .buttonStyle(.borderedProminent).tint(ReaderColor.action)
                            .disabled(audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .loading)
                            .accessibilityHint("Plays the recorded \(edition.kind == "weekly" ? "weekly recap" : "daily briefing")")
                    }

                    if let anchor = readingSectionID, edition.sections.contains(where: { $0.id == anchor }) {
                        Button("Continue reading") {
                            withAnimation(.easeInOut(duration: 0.25)) { scrollProxy.scrollTo(anchor, anchor: .top) }
                        }
                        .font(.subheadline.weight(.medium)).frame(minHeight: 44, alignment: .leading)
                    }

                    if let summary = edition.summary {
                        Text(summary).font(.system(size: summarySize)).lineSpacing(6)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Button("\(edition.coverage?.count ?? content.topics.count) topics · View coverage") {
                        onCoverage(edition)
                    }
                    .font(.subheadline.weight(.medium)).frame(minHeight: 44, alignment: .leading)

                    ArticleBody(sections: edition.sections, sources: content.sources,
                                coordinateSpace: Self.scrollSpace(edition), onSectionVisible: saveReadingPosition,
                                onSourceSelected: { selectedSource = $0 },
                                focusSectionID: focusSectionID,
                                exploreTopics: edition.kind == "daily" && onExploreTopic != nil ? content.topics.filter { topic in
                                    content.currentDeepDive(forTopic: topic.id) != nil
                                } : [],
                                onExploreTopic: onExploreTopic)
                    Text("You're up to date for this edition")
                        .font(.headline).padding(.top, 8)
                    Button {
                        isFinished.toggle()
                        UserDefaults.standard.set(isFinished, forKey: Self.finishedKey(edition))
                    } label: {
                        Label(isFinished ? "Finished" : "Mark finished",
                              systemImage: isFinished ? "checkmark.circle.fill" : "circle")
                            .frame(minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.horizontal, 20).padding(.vertical, 16)
            }
            .coordinateSpace(name: Self.scrollSpace(edition))
            .onAppear {
                onReaderVisible?(Self.identity(edition))
                guard !didInitializeReading else { return }
                didInitializeReading = true
                restoreReadingPosition(with: scrollProxy)
            }
        }
        .navigationTitle(navigationTitle)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedSource) { source in
            NavigationStack { SourceDetailView(source: source, sections: edition.sections) }
                .presentationDetents([.medium, .large])
        }
        .onAppear {
            #if DEBUG
            if let debugSourceID, selectedSource == nil {
                selectedSource = content.sources.first(where: { $0.id == debugSourceID })
            }
            #endif
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                LocalSaveButton(itemID: edition.id, revision: edition.revisionID ?? edition.id,
                                accessibilityTitle: edition.kind == "weekly" ? "Save recap" : "Save briefing")
                    .id("save-\(edition.id)-\(edition.revisionID ?? edition.id)")
            }
        }
    }

    private var track: AudioTrack? {
        edition.audio?.track(id: edition.id, title: edition.title,
                             kind: edition.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                             revisionID: edition.revisionID, scriptHash: edition.scriptHash)
    }

    private func correctionHeading(_ correction: EditionCorrection) -> String {
        guard let dateText = correction.correctedAt else { return "Material correction" }
        let input = DateFormatter()
        input.locale = Locale(identifier: "en_US_POSIX")
        input.timeZone = TimeZone(secondsFromGMT: 0)
        input.dateFormat = "yyyy-MM-dd"
        guard let date = input.date(from: String(dateText.prefix(10))) else { return "Material correction" }
        let output = DateFormatter()
        output.locale = Locale(identifier: "en_US")
        output.timeZone = TimeZone(secondsFromGMT: 0)
        output.dateFormat = "d MMMM"
        return "Correction · \(output.string(from: date))"
    }

    private var actionTitle: String {
        playbackActionTitle(track: track, state: audioState,
                            noun: edition.kind == "weekly" ? "recap" : "briefing")
    }

    private var metadata: String {
        let read = "\(readingMinutes) min read"
        guard let seconds = edition.audio?.durationSeconds,
              track != nil else { return read }
        return "\(max(1, Int((Double(seconds) / 60).rounded()))) min audio · \(read)"
    }

    private var readingMinutes: Int {
        max(1, Int(ceil(Double(edition.sections.reduce(0) { $0 + $1.body.split(separator: " ").count }) / 220)))
    }

    private static func finishedKey(_ edition: Edition) -> String {
        ReaderProgressKey.finished(itemID: edition.id, revisionID: edition.revisionID ?? "unversioned")
    }

    private func saveReadingPosition(_ sectionID: String) {
        guard didInitializeReading, !isRestoringReadingPosition, readingSectionID != sectionID else { return }
        readingSectionID = sectionID
        UserDefaults.standard.set(sectionID, forKey: Self.readingKey(edition))
    }

    private static func readingKey(_ edition: Edition) -> String {
        ReaderProgressKey.reading(itemID: edition.id, revisionID: edition.revisionID ?? "unversioned",
                                  scriptHash: edition.scriptHash)
    }

    private static func scrollSpace(_ edition: Edition) -> String {
        "reader.\(edition.id).\(edition.revisionID ?? "unversioned").\(edition.scriptHash ?? "unhashed")"
    }

    private static func identity(_ edition: Edition) -> AudioContentIdentity {
        AudioContentIdentity(kind: edition.kind == "weekly" ? .weeklyEdition : .dailyEdition,
                             id: edition.id, revision: edition.revisionID ?? "unversioned",
                             scriptHash: edition.scriptHash ?? "")
    }

    private func restoreReadingPosition(with proxy: ScrollViewProxy) {
        if edition.materialCorrection != nil {
            correctionHeadingFocused = true
            return
        }
        guard let anchor = readingSectionID,
              edition.sections.contains(where: { $0.id == anchor }) else {
            DispatchQueue.main.async { articleTitleFocused = true }
            return
        }
        isRestoringReadingPosition = true
        DispatchQueue.main.async {
            proxy.scrollTo(anchor, anchor: .top)
            focusSectionID = anchor
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { isRestoringReadingPosition = false }
        }
    }
}

func playbackActionTitle(track: AudioTrack?, state: AudioPlaybackState, noun: String) -> String {
    guard let track, state.track?.contentIdentity == track.contentIdentity else { return "Play \(noun)" }
    if state.status == .playing { return "Pause \(noun)" }
    if state.duration > 0 && state.position >= state.duration { return "Replay \(noun)" }
    if state.status == .loading { return "Loading…" }
    if state.position > 0 || state.status == .paused || state.status == .ready { return "Resume \(noun)" }
    return "Play \(noun)"
}

struct ArticleBody: View {
    let sections: [ArticleSection]
    var sources: [Source] = []
    var coordinateSpace: String? = nil
    var onSectionVisible: ((String) -> Void)? = nil
    var onSourceSelected: ((Source) -> Void)? = nil
    var focusSectionID: String? = nil
    var exploreTopics: [Topic] = []
    var onExploreTopic: ((Topic) -> Void)? = nil
    @ScaledMetric(relativeTo: .title2) private var sectionTitleSize: CGFloat = 21
    @ScaledMetric(relativeTo: .body) private var sectionBodySize: CGFloat = 18
    @AccessibilityFocusState private var focusedHeadingID: String?

    init(sections: [ArticleSection], sources: [Source] = [], coordinateSpace: String? = nil,
         onSectionVisible: ((String) -> Void)? = nil,
         onSourceSelected: ((Source) -> Void)? = nil,
         focusSectionID: String? = nil,
         exploreTopics: [Topic] = [], onExploreTopic: ((Topic) -> Void)? = nil) {
        self.sections = sections
        self.sources = sources
        self.coordinateSpace = coordinateSpace
        self.onSectionVisible = onSectionVisible
        self.onSourceSelected = onSourceSelected
        self.focusSectionID = focusSectionID
        self.exploreTopics = exploreTopics
        self.onExploreTopic = onExploreTopic
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            ForEach(sections) { section in
                VStack(alignment: .leading, spacing: 12) {
                    Text(section.heading).font(.system(size: sectionTitleSize, weight: .semibold))
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityFocused($focusedHeadingID, equals: section.id)
                    Text(section.body).font(.system(size: sectionBodySize)).lineSpacing(8).fixedSize(horizontal: false, vertical: true)
                    let references = (section.sourceIDs ?? []).compactMap { id in sources.first(where: { $0.id == id }) }
                    if !references.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(references) { source in
                                if let url = source.url {
                                    Link(source.title, destination: url)
                                        .font(.footnote.weight(.medium))
                                        .frame(minHeight: 44, alignment: .leading)
                                } else if let onSourceSelected {
                                    Button(source.title) { onSourceSelected(source) }
                                        .font(.footnote.weight(.medium))
                                        .frame(minHeight: 44, alignment: .leading)
                                        .accessibilityHint("Shows source details")
                                } else {
                                    Text(source.title).font(.footnote.weight(.medium))
                                }
                            }
                        }
                    }
                    if let topicIDs = section.topicIDs, let onExploreTopic {
                        ForEach(topicIDs, id: \.self) { topicID in
                            if let topic = exploreTopics.first(where: { $0.id == topicID }) {
                                Button { onExploreTopic(topic) } label: {
                                    Text("Read deeper: \(topic.label)")
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(ReaderColor.action)
                                        .frame(minHeight: 44, alignment: .leading)
                                }
                                .buttonStyle(.plain)
                                .accessibilityHint("Opens the reviewed explanation for \(topic.label)")
                            }
                        }
                    }
                }
                .id(section.id)
                .background {
                    if let coordinateSpace, onSectionVisible != nil {
                        GeometryReader { proxy in
                            Color.clear.preference(key: ReaderSectionFramesKey.self,
                                                   value: [section.id: proxy.frame(in: .named(coordinateSpace))])
                        }
                    }
                }
            }
        }
        .onPreferenceChange(ReaderSectionFramesKey.self) { frames in
            guard let coordinateSpace, onSectionVisible != nil else { return }
            let leadingSection = frames
                .filter { $0.value.minY <= 80 && $0.value.maxY > 0 }
                .max { $0.value.minY < $1.value.minY }
            if let sectionID = leadingSection?.key { onSectionVisible?(sectionID) }
        }
        .onChange(of: focusSectionID) { _, target in
            if let target { focusedHeadingID = target }
        }
    }
}

private struct ReaderSectionFramesKey: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]

    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, latest in latest })
    }
}

private struct SourceDetailView: View {
    let source: Source
    let sections: [ArticleSection]
    @Environment(\.dismiss) private var dismiss

    private var supportedSections: [ArticleSection] {
        let ids = Set(source.supportedSectionIDs ?? [source.supportedSection].compactMap { $0 })
        return sections.filter { ids.contains($0.id) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(source.title).font(.title2.weight(.semibold))
                if let evidence = source.evidence ?? source.note {
                    Text(evidence).font(.body).fixedSize(horizontal: false, vertical: true)
                }
                metadata("Publisher", source.publisher)
                metadata("Date", source.date)
                metadata("Kind", source.kind)
                if !supportedSections.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Supports this section").font(.subheadline.weight(.semibold))
                        ForEach(supportedSections) { Text($0.heading).font(.subheadline) }
                    }
                }
                metadata("Scope", source.scope)
                metadata("Availability", source.availability ?? (source.url == nil ? "Internal demonstration source · no external link" : "External source"))
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Source details")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { dismiss() }.frame(minWidth: 44, minHeight: 44)
            }
        }
    }

    @ViewBuilder
    private func metadata(_ label: String, _ value: String?) -> some View {
        if let value, !value.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(label).font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.secondary)
                Text(value).font(.subheadline).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct LocalSaveButton: View {
    private let itemID: String
    private let revision: String
    private let storageKey: String
    private let accessibilityTitle: String
    @State private var isSaved: Bool

    init(itemID: String, revision: String, accessibilityTitle: String) {
        self.itemID = itemID
        self.revision = revision
        let key = saveStorageKey(itemID: itemID, revision: revision)
        storageKey = key
        self.accessibilityTitle = accessibilityTitle
        _isSaved = State(initialValue: isSavedItem(itemID: itemID))
    }

    var body: some View {
        Button {
            isSaved.toggle()
            UserDefaults.standard.set(isSaved, forKey: storageKey)
            if isSaved {
                UserDefaults.standard.set(revision, forKey: savedRevisionKey(itemID: itemID))
            } else {
                UserDefaults.standard.removeObject(forKey: savedRevisionKey(itemID: itemID))
                removeLegacySaves(itemID: itemID)
            }
            NotificationCenter.default.post(name: .briefingSavedItemsChanged, object: nil)
        } label: {
            Label(isSaved ? "Saved" : "Save", systemImage: isSaved ? "bookmark.fill" : "bookmark")
                .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isSaved ? "Saved" : accessibilityTitle)
        .accessibilityHint(isSaved ? "Removes this item from saved items" : "Saves this item on this device")
        .onAppear { isSaved = isSavedItem(itemID: itemID) }
        .onReceive(NotificationCenter.default.publisher(for: .briefingSavedItemsChanged)) { _ in
            isSaved = isSavedItem(itemID: itemID)
        }
    }

}

private func saveStorageKey(itemID: String, revision: String) -> String {
    LocalItemStorageKey.saved(itemID: itemID)
}

func savedRevisionKey(itemID: String) -> String {
    "briefing.saved.revision.\(itemID)"
}

private func legacySavedRevision(itemID: String) -> String? {
    let prefix = "briefing.saved.\(itemID)."
    let revisions = UserDefaults.standard.dictionaryRepresentation().keys
        .filter { $0.hasPrefix(prefix) && UserDefaults.standard.bool(forKey: $0) }
    return revisions.count == 1 ? revisions.first.map { String($0.dropFirst(prefix.count)) } : nil
}

private func hasLegacySave(itemID: String) -> Bool {
    let prefix = "briefing.saved.\(itemID)."
    return UserDefaults.standard.dictionaryRepresentation().keys
        .contains(where: { $0.hasPrefix(prefix) && UserDefaults.standard.bool(forKey: $0) })
}

func savedRevision(itemID: String) -> String? {
    UserDefaults.standard.string(forKey: savedRevisionKey(itemID: itemID)) ?? legacySavedRevision(itemID: itemID)
}

func isSavedItem(itemID: String) -> Bool {
    UserDefaults.standard.bool(forKey: saveStorageKey(itemID: itemID, revision: "")) || hasLegacySave(itemID: itemID)
}

func removeLegacySaves(itemID: String) {
    let prefix = "briefing.saved.\(itemID)."
    for key in UserDefaults.standard.dictionaryRepresentation().keys where key.hasPrefix(prefix) {
        UserDefaults.standard.removeObject(forKey: key)
    }
}

private extension Notification.Name {
    static let briefingSavedItemsChanged = Notification.Name("briefing.saved-items-changed")
}

private struct SavedReaderItem: Identifiable {
    let id: String
    let title: String
    let subtitle: String
    let edition: Edition?
    let topic: Topic?
    var isCorrected: Bool { subtitle.localizedCaseInsensitiveContains("· Corrected") }
}

struct TopicsView: View {
    let content: BriefingContent
    @Binding var selectedTopic: Topic?
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    var body: some View {
        List(content.topics) { topic in
            Button { selectedTopic = topic } label: {
                VStack(alignment: .leading, spacing: 5) {
                    Text(topic.label).font(.body.weight(.semibold)).foregroundStyle(.primary)
                    Text(topic.scope).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                    if let dive = content.currentDeepDive(forTopic: topic.id) {
                        Text("\(dive.durationMinutes ?? 0) min · Reviewed \(dive.reviewedAt ?? "date unavailable") · \(dive.audio?.track(id: dive.id, title: dive.title, kind: .explanation, revisionID: dive.revisionID, scriptHash: dive.scriptHash) == nil ? "Audio pending approval" : "Recorded audio")")
                            .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    }
                }.padding(.vertical, 6).frame(minHeight: 44, alignment: .leading)
            }.buttonStyle(.plain)
        }
        .navigationTitle("Topics")
        .onAppear { onReaderVisible?(nil) }
    }
}

struct TopicDetailView: View {
    let topic: Topic
    let content: BriefingContent
    let onPlay: (AudioTrack) -> Void
    var audioState = AudioPlaybackState()
    var onReturnToToday: (() -> Void)? = nil
    var initialDiveID: String? = nil
    var transcriptReturnTitle: String? = nil
    var onTranscriptReturn: (() -> Void)? = nil
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    var usesNavigationBackButton = false
    @Environment(\.dismiss) private var dismiss
    @AccessibilityFocusState private var topicHeadingFocused: Bool
    @State private var readingSectionID: String?
    @State private var focusSectionID: String?
    @State private var isRestoringReadingPosition = false
    @State private var hasLoadedReadingPosition = false
    @ScaledMetric(relativeTo: .title) private var titleSize: CGFloat = 26
    @ScaledMetric(relativeTo: .body) private var summarySize: CGFloat = 18
    private var dive: DeepDive? {
        if let initialDiveID, let exact = content.currentDeepDive(id: initialDiveID) { return exact }
        return content.currentDeepDive(forTopic: topic.id)
    }
    var body: some View {
        ScrollViewReader { scrollProxy in
            ScrollView {
                if let dive {
                    VStack(alignment: .leading, spacing: 24) {
                    Text(topic.scope).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                    Text(dive.title).font(.system(size: titleSize, weight: .semibold))
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityFocused($topicHeadingFocused)
                    Text("\(dive.durationMinutes ?? 0) min read · Reviewed \(dive.reviewedAt ?? "date unavailable")")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    if let track = dive.audio?.track(id: dive.id, title: dive.title,
                                                     kind: .explanation,
                                                     revisionID: dive.revisionID, scriptHash: dive.scriptHash) {
                        Button {
                            onPlay(track)
                        } label: {
                            Label(topicActionTitle(for: dive), systemImage: topicActionSymbol(for: dive))
                                .frame(minHeight: 48).frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent).tint(ReaderColor.action)
                        .disabled(audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .loading)
                    } else {
                        Label("Audio pending approval", systemImage: "hourglass")
                            .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    }
                    if let summary = dive.summary {
                        Text(summary).font(.system(size: summarySize)).lineSpacing(8)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ArticleBody(sections: dive.sections, sources: content.sources,
                                coordinateSpace: readingSpace(dive), onSectionVisible: saveReadingPosition,
                                focusSectionID: focusSectionID)
                    }.padding(20)
                } else {
                    ContentUnavailableView("Explanation unavailable", systemImage: "text.page", description: Text("There is no reviewed explanation for this topic in the sample pack."))
                }
            }
            .coordinateSpace(name: dive.map { readingSpace($0) } ?? "reader.explanation.unavailable")
            .onAppear {
                guard let dive else { return }
                onReaderVisible?(identity(dive))
                guard !hasLoadedReadingPosition else { return }
                readingSectionID = UserDefaults.standard.string(forKey: readingKey(dive))
                hasLoadedReadingPosition = true
                restoreReadingPosition(with: scrollProxy)
            }
        }
        .navigationTitle(topic.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !usesNavigationBackButton {
                ToolbarItem(placement: .topBarLeading) {
                    if let onReturnToToday {
                        ContextReturnButton(title: "Back to Today", action: onReturnToToday)
                    } else if let transcriptReturnTitle, let onTranscriptReturn {
                        ContextReturnButton(title: transcriptReturnTitle, action: onTranscriptReturn)
                    } else {
                        Button("Close") { dismiss() }.frame(minWidth: 44, minHeight: 44)
                    }
                }
            }
            if let dive {
                ToolbarItem(placement: .topBarTrailing) {
                    LocalSaveButton(itemID: dive.id, revision: dive.revisionID ?? dive.id,
                                    accessibilityTitle: "Save explanation")
                        .id("save-\(dive.id)-\(dive.revisionID ?? dive.id)")
                }
            }
        }
    }

    private func topicActionTitle(for dive: DeepDive) -> String {
        playbackActionTitle(track: track(for: dive), state: audioState, noun: "explanation")
    }

    private func topicActionSymbol(for dive: DeepDive) -> String {
        audioState.track?.contentIdentity == track(for: dive)?.contentIdentity && audioState.status == .playing
            ? "pause.fill" : "play.fill"
    }

    private func track(for dive: DeepDive) -> AudioTrack? {
        dive.audio?.track(id: dive.id, title: dive.title, kind: .explanation,
                          revisionID: dive.revisionID, scriptHash: dive.scriptHash)
    }

    private func readingSpace(_ dive: DeepDive) -> String {
        "reader.\(dive.id).\(dive.revisionID ?? "unversioned").\(dive.scriptHash ?? "unhashed")"
    }

    private func readingKey(_ dive: DeepDive) -> String {
        ReaderProgressKey.reading(itemID: dive.id, revisionID: dive.revisionID ?? "unversioned",
                                  scriptHash: dive.scriptHash)
    }

    private func identity(_ dive: DeepDive) -> AudioContentIdentity {
        AudioContentIdentity(kind: .explanation, id: dive.id,
                             revision: dive.revisionID ?? "unversioned", scriptHash: dive.scriptHash ?? "")
    }

    private func restoreReadingPosition(with proxy: ScrollViewProxy) {
        guard let dive else { return }
        guard let anchor = readingSectionID,
              dive.sections.contains(where: { $0.id == anchor }) else {
            topicHeadingFocused = true
            return
        }
        isRestoringReadingPosition = true
        DispatchQueue.main.async {
            proxy.scrollTo(anchor, anchor: .top)
            focusSectionID = anchor
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { isRestoringReadingPosition = false }
        }
    }

    private func saveReadingPosition(_ sectionID: String) {
        guard hasLoadedReadingPosition, !isRestoringReadingPosition,
              readingSectionID != sectionID, let dive else { return }
        readingSectionID = sectionID
        UserDefaults.standard.set(sectionID, forKey: readingKey(dive))
    }
}

struct ContextReturnButton: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                if dynamicTypeSize.isAccessibilitySize {
                    Image(systemName: "chevron.left")
                        .frame(minWidth: 44, minHeight: 44)
                } else {
                    Text(title).lineLimit(1).frame(minHeight: 44)
                }
            }
            .contentShape(Rectangle())
        }
        .accessibilityLabel(title)
    }
}

struct LibraryView: View {
    let content: BriefingContent
    var audioState = AudioPlaybackState()
    var onPlay: (AudioTrack) -> Void = { _ in }
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    @State private var selectedEdition: Edition?
    @State private var savedItems: [SavedReaderItem] = []
    private var editions: [Edition] { content.currentEditions }
    private var savedEditions: [SavedReaderItem] { savedItems.filter { $0.edition != nil } }
    private var savedExplanations: [SavedReaderItem] { savedItems.filter { $0.topic != nil } }

    var body: some View {
        List {
            if !savedItems.isEmpty {
                Section("Saved") {
                    ForEach(savedEditions) { item in
                        if let edition = item.edition {
                            NavigationLink {
                                EditionReaderView(edition: edition, content: content,
                                                  navigationTitle: edition.kind == "weekly" ? "Weekly" : "Edition",
                                                  audioState: audioState, onPlay: onPlay,
                                                  onCoverage: { selectedEdition = $0 },
                                                  onReaderVisible: onReaderVisible)
                            } label: { savedRow(item) }
                        }
                    }
                    ForEach(savedExplanations) { item in
                        if let topic = item.topic {
                            NavigationLink {
                                TopicDetailView(topic: topic, content: content, onPlay: onPlay, audioState: audioState,
                                                onReaderVisible: onReaderVisible)
                            } label: { savedRow(item) }
                        }
                    }
                }
            }
            Section("Editions") {
                ForEach(editions) { edition in
                    NavigationLink {
                        EditionReaderView(edition: edition, content: content,
                                          navigationTitle: edition.kind == "weekly" ? "Weekly" : "Edition",
                                          audioState: audioState, onPlay: onPlay,
                                          onCoverage: { selectedEdition = $0 },
                                          onReaderVisible: onReaderVisible)
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(edition.kind == "weekly" ? "Weekly recap" : "Daily briefing")
                                .font(.footnote.weight(.medium)).foregroundStyle(ReaderColor.secondary)
                            Text(edition.title).font(.body.weight(.semibold))
                            Text(edition.localDate ?? edition.weekStart ?? edition.asOf ?? "Date unavailable")
                                .font(.footnote).foregroundStyle(ReaderColor.secondary)
                        }.padding(.vertical, 6)
                    }
                }
            }
        }
        .navigationTitle("Library")
        .sheet(item: $selectedEdition) { edition in
            NavigationStack { CoverageView(edition: edition, content: content, audioState: audioState, onPlay: onPlay,
                                           onReaderVisible: onReaderVisible) }
        }
        .overlay {
            if editions.isEmpty { ContentUnavailableView("Library is empty", systemImage: "books.vertical", description: Text("Past editions will appear here.")) }
        }
        .onAppear { onReaderVisible?(nil); loadSavedItems() }
        .onChange(of: content.currentEdition(kind: "daily")?.revisionID) { _, _ in loadSavedItems() }
        .onReceive(NotificationCenter.default.publisher(for: .briefingSavedItemsChanged)) { _ in loadSavedItems() }
    }

    private func savedRow(_ item: SavedReaderItem) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Text(item.subtitle.replacingOccurrences(of: " · Corrected", with: ""))
                    .font(.footnote.weight(.medium)).foregroundStyle(ReaderColor.secondary)
                if item.isCorrected {
                    Text("Corrected").font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.action)
                }
            }
            Text(item.title).font(.body.weight(.semibold))
        }.padding(.vertical, 6).frame(minHeight: 44, alignment: .leading)
    }

    private func loadSavedItems() {
        var results: [SavedReaderItem] = []
        for edition in editions where isSavedItem(itemID: edition.id) {
            let savedAt = savedRevision(itemID: edition.id)
            let revision = edition.revisionID ?? edition.id
            results.append(SavedReaderItem(id: "edition-\(edition.id)", title: edition.title,
                                           subtitle: (edition.kind == "weekly" ? "Weekly recap" : "Daily briefing") + (edition.materialCorrection != nil ? " · Corrected" : "") + (savedAt != nil && savedAt != revision ? " · Updated since saved" : ""),
                                           edition: edition, topic: nil))
        }
        for dive in content.currentDeepDives where isSavedItem(itemID: dive.id) {
            guard let topic = content.topics.first(where: { $0.id == dive.topicID }) else { continue }
            let savedAt = savedRevision(itemID: dive.id)
            let revision = dive.revisionID ?? dive.id
            results.append(SavedReaderItem(id: "explanation-\(dive.id)", title: dive.title,
                                           subtitle: "Explanation · \(topic.label)" + (savedAt != nil && savedAt != revision ? " · Corrected" : ""), edition: nil, topic: topic))
        }
        savedItems = results
    }
}

struct CoverageView: View {
    let edition: Edition
    let content: BriefingContent
    var audioState = AudioPlaybackState()
    var onPlay: (AudioTrack) -> Void = { _ in }
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        List {
            ForEach(edition.coverage ?? []) { row in
                VStack(alignment: .leading, spacing: 5) {
                    if let topic = content.topics.first(where: { $0.id == row.topicID }) {
                        NavigationLink {
                            TopicDetailView(topic: topic, content: content, onPlay: onPlay, audioState: audioState,
                                            onReaderVisible: onReaderVisible)
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(topic.label).font(.body.weight(.semibold))
                                Text("Explore \(topic.label)").font(.footnote.weight(.medium)).foregroundStyle(ReaderColor.action)
                            }
                        }
                    } else {
                        Text(row.topicID).font(.body.weight(.semibold))
                    }
                    Text(row.state.capitalized).font(.subheadline)
                    if let reason = row.reason { Text(reason).font(.footnote).foregroundStyle(ReaderColor.secondary) }
                }.padding(.vertical, 5)
            }
            if edition.coverage == nil {
                ForEach(content.topics) { topic in
                    NavigationLink {
                        TopicDetailView(topic: topic, content: content, onPlay: onPlay, audioState: audioState)
                    } label: {
                        VStack(alignment: .leading) {
                            Text(topic.label).font(.body.weight(.semibold))
                            Text("Coverage detail unavailable · Explore \(topic.label)").font(.footnote).foregroundStyle(ReaderColor.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("View coverage")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Close") { dismiss() }.frame(minWidth: 44, minHeight: 44) } }
        .onAppear { onReaderVisible?(nil) }
    }
}
