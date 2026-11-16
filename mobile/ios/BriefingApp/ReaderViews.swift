import SwiftUI
import SafariServices

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
    var onOpenSource: ((Source) -> Void)? = nil
    var onOpenWeekly: ((Edition) -> Void)? = nil
    var initialSectionID: String? = nil
    var debugSourceID: String? = nil
    var onOpenNextStory: ((String) -> Void)? = nil
    @State private var isFinished: Bool
    @State private var readingSectionID: String?
    @State private var selectedSource: Source?
    @State private var focusSectionID: String?
    @State private var isRestoringReadingPosition = false
    @State private var didInitializeReading = false
    @State private var showingContents = false
    @State private var contentsSelection: String?
    @AccessibilityFocusState private var articleTitleFocused: Bool
    @AccessibilityFocusState private var correctionHeadingFocused: Bool
    @ScaledMetric(relativeTo: .title) private var titleSize: CGFloat = 30
    @ScaledMetric(relativeTo: .body) private var summarySize: CGFloat = 18

    init(edition: Edition, content: BriefingContent, navigationTitle: String,
         audioState: AudioPlaybackState = AudioPlaybackState(),
         onPlay: @escaping (AudioTrack) -> Void = { _ in },
         onCoverage: @escaping (Edition) -> Void = { _ in },
         onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil,
         onExploreTopic: ((Topic) -> Void)? = nil,
         onOpenSource: ((Source) -> Void)? = nil,
         onOpenWeekly: ((Edition) -> Void)? = nil,
         initialSectionID: String? = nil,
         debugSourceID: String? = nil,
         onOpenNextStory: ((String) -> Void)? = nil) {
        self.edition = edition
        self.content = content
        self.navigationTitle = navigationTitle
        self.audioState = audioState
        self.onPlay = onPlay
        self.onCoverage = onCoverage
        self.onReaderVisible = onReaderVisible
        self.onExploreTopic = onExploreTopic
        self.onOpenSource = onOpenSource
        self.onOpenWeekly = onOpenWeekly
        self.initialSectionID = initialSectionID
        self.debugSourceID = debugSourceID
        self.onOpenNextStory = onOpenNextStory
        let key = Self.finishedKey(edition)
        _isFinished = State(initialValue: UserDefaults.standard.bool(forKey: key))
        _readingSectionID = State(initialValue: initialSectionID ?? UserDefaults.standard.string(forKey: Self.readingKey(edition)))
    }

    var body: some View {
        Group {
            if edition.kind == "daily" {
                FocusedStoryReader(edition: edition, content: content, sectionID: initialSectionID ?? edition.readingSections.first?.id,
                                   audioState: audioState, onPlay: onPlay, onExploreTopic: onExploreTopic,
                                   onOpenSource: onOpenSource, onOpenNext: { nextSection in onOpenNextStory?(nextSection) })
            } else {
                legacyBody
            }
        }
    }

    private var legacyBody: some View {
        ScrollViewReader { scrollProxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("\(edition.localDate ?? edition.weekStart ?? edition.asOf ?? "Edition") · Sample · Fictional content")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)

                    Text(edition.title).font(.system(size: titleSize, weight: .semibold, design: .serif))
                        .accessibilityHeading(.h1)
                        .accessibilityFocused($articleTitleFocused)
                    Text(metadata)
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
                                Text(audioState.status == .superseded && audioState.track?.id == edition.id
                                     ? "Playback stopped. Updated audio is unavailable for this corrected edition."
                                     : "Updated audio is unavailable for this corrected edition.")
                                    .font(.footnote).foregroundStyle(ReaderColor.secondary)
                            }
                            Button("Read the corrected section") {
                                guard let anchor = correction.sectionIDs.first,
                                      edition.readingSections.contains(where: { $0.id == anchor }) else { return }
                                if reduceMotion { scrollProxy.scrollTo(anchor, anchor: .top) }
                                else { withAnimation(.easeInOut(duration: 0.25)) { scrollProxy.scrollTo(anchor, anchor: .top) } }
                                focusSectionID = nil
                                DispatchQueue.main.async { focusSectionID = anchor }
                            }
                            .font(.subheadline.weight(.medium)).frame(minHeight: 44, alignment: .leading)
                            ForEach(correction.sourceIDs, id: \.self) { sourceID in
                                if let source = content.sources.first(where: { $0.id == sourceID }) {
                                    Button {
                                        if let onOpenSource { onOpenSource(source) }
                                        else { selectedSource = source }
                                    } label: {
                                        Text("Evidence: \(source.title)")
                                            .font(.footnote.weight(.medium))
                                            .multilineTextAlignment(.leading)
                                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                    }
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
                            .buttonStyle(.borderedProminent).tint(ReaderColor.primaryAction)
                            .disabled(audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .loading)
                            .accessibilityHint("Plays the recorded \(edition.kind == "weekly" ? "weekly recap" : "daily briefing")")
                    }

                    if let anchor = readingSectionID, edition.readingSections.contains(where: { $0.id == anchor }) {
                        Button("Continue reading") {
                            withAnimation(.easeInOut(duration: 0.25)) { scrollProxy.scrollTo(anchor, anchor: .top) }
                        }
                        .font(.subheadline.weight(.medium)).frame(minHeight: 44, alignment: .leading)
                    }

                    if let summary = edition.summary {
                        Text(summary).font(.system(size: summarySize)).lineSpacing(6)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    ArticleBody(sections: edition.readingSections, sources: content.sources,
                                coordinateSpace: Self.scrollSpace(edition), onSectionVisible: saveReadingPosition,
                                onSourceSelected: { source in
                                    if let onOpenSource { onOpenSource(source) }
                                    else { selectedSource = source }
                                },
                                focusSectionID: focusSectionID,
                                showSectionNumbers: true,
                                exploreTopics: onExploreTopic != nil ? content.topics.filter { topic in
                                    content.currentDeepDive(forTopic: topic.id) != nil
                                } : [],
                                explanationTitles: Dictionary(uniqueKeysWithValues: content.currentDeepDives.map { ($0.topicID, $0.title) }),
                                onExploreTopic: onExploreTopic)
                    Text(edition.kind == "weekly" ? "End of this week's recap" : "End of today's briefing")
                        .font(.headline).padding(.top, 8)
                    LocalSaveButton(itemID: edition.id, revision: edition.revisionID ?? edition.id,
                                    accessibilityTitle: edition.kind == "weekly" ? "Save recap" : "Save briefing")
                        .id("save-\(edition.id)-\(edition.revisionID ?? edition.id)")
                    if edition.kind == "daily", let weekly = content.currentEdition(kind: "weekly"), let onOpenWeekly {
                        Button("Read the weekly recap") { onOpenWeekly(weekly) }
                            .frame(minHeight: 44, alignment: .leading)
                    }
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
            .onChange(of: contentsSelection) { _, target in
                guard let target else { return }
                if reduceMotion { scrollProxy.scrollTo(target, anchor: .top) }
                else { withAnimation(.easeInOut(duration: 0.25)) { scrollProxy.scrollTo(target, anchor: .top) } }
                focusSectionID = nil
                DispatchQueue.main.async {
                    focusSectionID = target
                    contentsSelection = nil
                }
            }
        }
        .navigationTitle(navigationTitle)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedSource) { source in
            NavigationStack { SourceDetailView(source: source, modal: true) }
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showingContents) {
            NavigationStack {
                List(edition.readingSections.indices, id: \.self) { index in
                    let section = edition.readingSections[index]
                    Button {
                        showingContents = false
                        contentsSelection = section.id
                    } label: {
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text(String(format: "%02d", index + 1)).foregroundStyle(ReaderColor.secondary)
                            Text(section.heading).foregroundStyle(.primary)
                        }.frame(minHeight: 44, alignment: .leading)
                    }
                }
                .navigationTitle("Contents")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { showingContents = false } } }
            }
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
                Button("Contents") { showingContents = true }.frame(minHeight: 44)
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
        edition.readingMinutes
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
              edition.readingSections.contains(where: { $0.id == anchor }) else {
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

private struct FocusedStoryReader: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let edition: Edition
    let content: BriefingContent
    let sectionID: String?
    let audioState: AudioPlaybackState
    let onPlay: (AudioTrack) -> Void
    let onExploreTopic: ((Topic) -> Void)?
    let onOpenSource: ((Source) -> Void)?
    let onOpenNext: ((String) -> Void)?
    @ScaledMetric(relativeTo: .largeTitle) private var headlineSize: CGFloat = 30
    @ScaledMetric(relativeTo: .title) private var nextTitleSize: CGFloat = 20

    private var sections: [ArticleSection] { edition.readingSections }
    private var index: Int { max(0, sections.firstIndex(where: { $0.id == sectionID }) ?? 0) }
    private var progressTitle: String {
        dynamicTypeSize >= .xxxLarge ? "\(index + 1) of \(sections.count)" : "Story \(index + 1) of \(sections.count)"
    }
    private var section: ArticleSection? { sections.indices.contains(index) ? sections[index] : nil }
    private var storyText: (whatHappened: String, whyItMatters: String?) {
        let paragraphs = (section?.body ?? "This story is unavailable.")
            .components(separatedBy: "\n\n")
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard let first = paragraphs.first else { return ("This story is unavailable.", nil) }
        let context = paragraphs.dropFirst().joined(separator: "\n\n")
        return (first, context.isEmpty ? nil : context)
    }
    private var track: AudioTrack? {
        // A material correction invalidates the edition recording as one unit.
        guard edition.materialCorrection == nil else { return nil }
        return edition.audio?.track(id: edition.id, title: edition.title, kind: .dailyEdition,
                                    revisionID: edition.revisionID, scriptHash: edition.scriptHash)
    }
    private var correction: EditionCorrection? {
        guard let section else { return nil }
        return edition.materialCorrection.flatMap { $0.sectionIDs.contains(section.id) ? $0 : nil }
    }
    private var topicLabel: String {
        guard let topicID = section?.topicIDs?.first,
              let topic = content.topics.first(where: { $0.id == topicID }) else { return "Briefing" }
        return topic.label
    }
    private var sourceReferences: [Source] {
        guard let section else { return [] }
        return (section.sourceIDs ?? []).compactMap { sourceID in
            content.sources.first(where: { $0.id == sourceID })
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                progressHeader
                Text(edition.title)
                    .font(.footnote)
                    .foregroundStyle(ReaderColor.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(topicLabel).font(.caption.weight(.semibold)).tracking(0.8).foregroundStyle(ReaderColor.secondary)
                storyHeadline
                correctionNotice
                listenButton
                storyTextView
                sourceList
                explanationLink
                nextStoryControl
            }
            .padding(.horizontal, 20).padding(.vertical, 24)
            .frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity)
        }
        .navigationTitle(progressTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) {
            LocalSaveButton(itemID: edition.id + "." + (section?.id ?? "story"), revision: edition.revisionID ?? edition.id, accessibilityTitle: "Save story")
        }}
    }

    private var progressHeader: some View {
        HStack(spacing: 8) {
            Text(progressTitle).font(.subheadline.weight(.semibold)).foregroundStyle(ReaderColor.secondary)
            if dynamicTypeSize < .xxxLarge {
                HStack(spacing: 5) {
                    ForEach(sections.indices, id: \.self) { dot in
                        Circle()
                            .fill(dot <= index ? ReaderColor.action : ReaderColor.secondary.opacity(0.3))
                            .frame(width: 6, height: 6)
                            .accessibilityHidden(true)
                    }
                }
                .accessibilityHidden(true)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Story \(index + 1) of \(sections.count)")
    }

    private var storyHeadline: some View {
        Text(section?.heading ?? edition.title)
            .font(.system(size: headlineSize, weight: .semibold, design: .serif))
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityHeading(.h1)
    }

    @ViewBuilder
    private var correctionNotice: some View {
        if let correction {
            VStack(alignment: .leading, spacing: 8) {
                Text(correctionLabel(correction))
                    .font(.caption.weight(.bold)).tracking(0.7).foregroundStyle(ReaderColor.correction)
                Text(correction.whatChanged).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                Text(correction.whyItMatters).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                Text("Audio paused: the corrected edition recording is unavailable until it is re-recorded.")
                    .font(.footnote).foregroundStyle(ReaderColor.secondary)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(ReaderColor.correction.opacity(0.10), in: RoundedRectangle(cornerRadius: 12))
        }
    }

    @ViewBuilder
    private var listenButton: some View {
        if let track {
            Button("Listen from here") { onPlay(track) }
                .font(.headline).frame(maxWidth: .infinity, minHeight: 48)
                .buttonStyle(.borderedProminent).tint(ReaderColor.primaryAction)
                .disabled(audioState.status == .superseded || isLoadingCurrentTrack(track))
        }
    }

    private var storyTextView: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("What happened").font(.headline).accessibilityHeading(.h2)
            Text(storyText.whatHappened)
                .font(.system(.body, design: .serif)).lineSpacing(7).fixedSize(horizontal: false, vertical: true)
            if let whyItMatters = storyText.whyItMatters {
                Text("Why it matters").font(.headline).accessibilityHeading(.h2)
                Text(whyItMatters)
                    .font(.system(.body, design: .serif)).lineSpacing(7).fixedSize(horizontal: false, vertical: true)
            }
            Text("Things to consider").font(.headline).accessibilityHeading(.h2)
            Text("This report is for general information and does not offer personal financial or health advice.")
                .font(.footnote).foregroundStyle(ReaderColor.secondary)
        }
    }

    @ViewBuilder
    private var sourceList: some View {
        if !sourceReferences.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Sources").font(.headline).accessibilityHeading(.h2)
                ForEach(sourceReferences.indices, id: \.self) { index in
                    sourceButton(sourceReferences[index], number: index + 1)
                }
            }
        }
    }

    private func sourceButton(_ source: Source, number: Int) -> some View {
        Button { onOpenSource?(source) } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("\(number)").font(.caption.monospacedDigit()).foregroundStyle(ReaderColor.secondary)
                VStack(alignment: .leading, spacing: 3) {
                    Text(source.publisher ?? "Source").font(.caption).foregroundStyle(ReaderColor.secondary)
                    Text(source.title).font(.subheadline.weight(.medium)).foregroundStyle(ReaderColor.action)
                }
                Spacer()
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var explanationLink: some View {
        if let topicID = section?.topicIDs?.first,
           let topic = content.topics.first(where: { $0.id == topicID }),
           let deepDive = content.currentDeepDive(forTopic: topic.id) {
            Button { onExploreTopic?(topic) } label: {
                VStack(alignment: .leading, spacing: 5) {
                    Text("UNDERSTAND THIS BETTER")
                        .font(.caption.weight(.semibold)).tracking(0.7).foregroundStyle(ReaderColor.secondary)
                    Text(deepDive.title).font(.subheadline.weight(.semibold))
                }
                .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
            }
            .buttonStyle(.plain)
        }
    }

    @ViewBuilder
    private var nextStoryControl: some View {
        if index + 1 < sections.count {
            let next = sections[index + 1]
            Button { onOpenNext?(next.id) } label: {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Next story").font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.secondary)
                    Text(next.heading).font(.system(size: nextTitleSize, weight: .semibold, design: .serif))
                }
                .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
            }
            .buttonStyle(.plain)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("You're up to date").font(.headline)
                Button("Mark finished") {
                    UserDefaults.standard.set(true, forKey: ReaderProgressKey.finished(
                        itemID: edition.id, revisionID: edition.revisionID ?? "unversioned"))
                }
                .frame(minHeight: 44, alignment: .leading)
            }
        }
    }

    private func isLoadingCurrentTrack(_ track: AudioTrack) -> Bool {
        audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .loading
    }

    private func correctionLabel(_ correction: EditionCorrection) -> String {
        guard let raw = correction.correctedAt,
              let date = ISO8601DateFormatter().date(from: raw) else { return "CORRECTED" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "d MMM, h:mm a zzz"
        return "CORRECTED · \(formatter.string(from: date))"
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
    var showSectionNumbers = false
    var exploreTopics: [Topic] = []
    var explanationTitles: [String: String] = [:]
    var onExploreTopic: ((Topic) -> Void)? = nil
    @ScaledMetric(relativeTo: .title2) private var sectionTitleSize: CGFloat = 24
    @ScaledMetric(relativeTo: .body) private var sectionBodySize: CGFloat = 18
    @AccessibilityFocusState private var focusedHeadingID: String?

    init(sections: [ArticleSection], sources: [Source] = [], coordinateSpace: String? = nil,
         onSectionVisible: ((String) -> Void)? = nil,
         onSourceSelected: ((Source) -> Void)? = nil,
         focusSectionID: String? = nil,
         showSectionNumbers: Bool = false,
         exploreTopics: [Topic] = [], explanationTitles: [String: String] = [:],
         onExploreTopic: ((Topic) -> Void)? = nil) {
        self.sections = sections
        self.sources = sources
        self.coordinateSpace = coordinateSpace
        self.onSectionVisible = onSectionVisible
        self.onSourceSelected = onSourceSelected
        self.focusSectionID = focusSectionID
        self.showSectionNumbers = showSectionNumbers
        self.exploreTopics = exploreTopics
        self.explanationTitles = explanationTitles
        self.onExploreTopic = onExploreTopic
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 40) {
            ForEach(sections.indices, id: \.self) { index in
                let section = sections[index]
                VStack(alignment: .leading, spacing: 12) {
                    if showSectionNumbers {
                        Text(String(format: "%02d / %02d", index + 1, sections.count))
                            .font(.caption.monospacedDigit().weight(.semibold))
                            .foregroundStyle(ReaderColor.secondary)
                    }
                    Text(section.heading).font(.system(size: sectionTitleSize, weight: .semibold, design: .serif))
                        .accessibilityHeading(.h2)
                        .accessibilityFocused($focusedHeadingID, equals: section.id)
                    Text(section.body).font(.system(size: sectionBodySize)).lineSpacing(8).fixedSize(horizontal: false, vertical: true)
                    if let topicIDs = section.topicIDs, let onExploreTopic {
                        ForEach(topicIDs, id: \.self) { topicID in
                            if let topic = exploreTopics.first(where: { $0.id == topicID }) {
                                Button { onExploreTopic(topic) } label: {
                                    HStack(spacing: 12) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text("UNDERSTAND THIS BETTER")
                                                .font(.caption.weight(.semibold)).tracking(0.7)
                                                .foregroundStyle(ReaderColor.secondary)
                                            Text(explanationTitles[topicID] ?? topic.label)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundStyle(ReaderColor.action)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                        Spacer(minLength: 0)
                                        Image(systemName: "chevron.right")
                                            .font(.caption.weight(.semibold))
                                            .foregroundStyle(ReaderColor.action)
                                    }
                                    .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .accessibilityHint("Opens the reviewed explanation for \(topic.label)")
                            }
                        }
                    }
                    let references = (section.sourceIDs ?? []).compactMap { id in sources.first(where: { $0.id == id }) }
                    if !references.isEmpty {
                        DisclosureGroup("Sources (\(references.count))") {
                            VStack(alignment: .leading, spacing: 4) {
                            ForEach(references) { source in
                                if let onSourceSelected {
                                    Button { onSourceSelected(source) } label: {
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(source.publisher ?? "Source")
                                                .font(.caption).foregroundStyle(ReaderColor.secondary)
                                            Text(source.title).font(.subheadline.weight(.medium))
                                                .foregroundStyle(ReaderColor.action)
                                        }
                                        .frame(minHeight: 44, alignment: .leading)
                                    }
                                    .accessibilityHint("Opens source details")
                                } else {
                                    Text(source.title).font(.footnote.weight(.medium))
                                }
                            }
                            }
                        }
                        .tint(ReaderColor.action)
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

struct SourceDetailView: View {
    let source: Source
    var modal = false
    @Environment(\.dismiss) private var dismiss
    @State private var showingOriginal = false
    @ScaledMetric(relativeTo: .largeTitle) private var titleSize: CGFloat = 30

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text(source.publisher ?? source.kind ?? source.url?.host ?? "Fictional demonstration source")
                    .font(.caption.weight(.semibold)).tracking(1)
                    .foregroundStyle(ReaderColor.secondary)
                Text(source.title)
                    .font(.system(size: titleSize, weight: .semibold, design: .serif))
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityHeading(.h1)
                if source.url != nil {
                    Button {
                        showingOriginal = true
                    } label: {
                        Label("Open original", systemImage: "arrow.up.right")
                            .frame(minHeight: 44, alignment: .leading)
                    }
                    .accessibilityHint("Opens the original source in an in-app browser")
                }
                Divider()
                Text("WHAT THIS SUPPORTS")
                    .font(.caption.weight(.semibold)).tracking(1)
                    .foregroundStyle(ReaderColor.secondary)
                if let evidence = source.evidence ?? source.note {
                    Text(evidence).font(.body).fixedSize(horizontal: false, vertical: true)
                }
                if let scope = source.scope, !scope.isEmpty {
                    Text("LIMITS")
                        .font(.caption.weight(.semibold)).tracking(1)
                        .foregroundStyle(ReaderColor.secondary)
                        .padding(.top, 8)
                    Text(scope).font(.body).fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("LIMITS")
                        .font(.caption.weight(.semibold)).tracking(1)
                        .foregroundStyle(ReaderColor.secondary)
                        .padding(.top, 8)
                    Text("This source provides background context; it does not verify the fictional scenario.")
                        .font(.body).foregroundStyle(ReaderColor.secondary)
                }
                if source.url == nil {
                    Text(source.availability ?? "Fictional demonstration source; no external original.")
                        .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                }
                if let date = source.date, !date.isEmpty { metadata("Published", date) }
                Text("Sample · fictional").font(.caption).foregroundStyle(ReaderColor.secondary)
                if let host = source.url?.host { Text(host).font(.footnote).foregroundStyle(ReaderColor.secondary) }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Source")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showingOriginal) {
            if let url = source.url { SafariPage(url: url) }
        }
        .toolbar {
            if modal {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.frame(minWidth: 44, minHeight: 44)
                }
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

private struct SafariPage: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }
    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
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
    var isUpdated: Bool { subtitle.localizedCaseInsensitiveContains("· Updated since saved") }
}

struct TopicsView: View {
    let content: BriefingContent
    @Binding var selectedTopic: Topic?
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    var onOpenTopic: ((Topic) -> Void)? = nil
    var onManageTopics: (() -> Void)? = nil
    @ScaledMetric(relativeTo: .body) private var introSize: CGFloat = 18
    @ScaledMetric(relativeTo: .title) private var diveTitleSize: CGFloat = 21
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("Background for the stories you read.")
                    .font(.system(size: introSize)).foregroundStyle(ReaderColor.secondary)
                    .padding(.bottom, 26)
                ForEach(content.topics) { topic in
                    if let dive = content.currentDeepDive(forTopic: topic.id) {
                        Button {
                            if let onOpenTopic { onOpenTopic(topic) }
                            else { selectedTopic = topic }
                        } label: {
                            HStack(alignment: .top, spacing: 12) {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(topic.label.uppercased())
                                        .font(.caption.weight(.semibold)).tracking(0.8)
                                        .foregroundStyle(ReaderColor.secondary)
                                    Text(dive.title)
                                        .font(.system(size: diveTitleSize, weight: .semibold, design: .serif))
                                        .foregroundStyle(.primary)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .accessibilityHeading(.h2)
                                    Text("\(dive.durationMinutes ?? 0) min explanation")
                                        .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                                }
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(ReaderColor.secondary)
                            }
                            .padding(.vertical, 18)
                            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        Divider()
                    }
                }
                Text("Topic choices are saved on this device. This fixed sample briefing does not regenerate when they change.")
                    .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    .padding(.top, 24)
            }
            .padding(.horizontal, 20).padding(.top, 24).padding(.bottom, 36)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Topics")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let onManageTopics {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: onManageTopics) {
                        Image(systemName: "gearshape").frame(minWidth: 44, minHeight: 44)
                    }.accessibilityLabel("Settings")
                }
            }
        }
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
    var onOpenSource: ((Source) -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var selectedSource: Source?
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
                    VStack(alignment: .leading, spacing: 20) {
                    Text("\(topic.label.uppercased()) · EXPLANATION")
                        .font(.caption.weight(.semibold)).tracking(0.8)
                        .foregroundStyle(ReaderColor.secondary)
                    Text(dive.title).font(.system(size: titleSize, weight: .semibold, design: .serif))
                        .accessibilityHeading(.h1)
                        .accessibilityFocused($topicHeadingFocused)
                    Text("\(dive.durationMinutes ?? 0) min read")
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
                        .buttonStyle(.borderedProminent).tint(ReaderColor.primaryAction)
                        .disabled(audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .loading)
                    }
                    if let summary = dive.summary {
                        Text(summary).font(.system(size: summarySize)).lineSpacing(8)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ArticleBody(sections: dive.sections, sources: content.sources,
                                coordinateSpace: readingSpace(dive), onSectionVisible: saveReadingPosition,
                                onSourceSelected: { source in
                                    if let onOpenSource { onOpenSource(source) }
                                    else { selectedSource = source }
                                },
                                focusSectionID: focusSectionID)
                    Text("Background explanation; examples in this sample are illustrative. \(topic.scope)")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    if let reviewedAt = dive.reviewedAt {
                        Text("Reviewed \(reviewedAt)")
                            .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    }
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
        .navigationTitle("Explanation")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedSource) { source in
            NavigationStack { SourceDetailView(source: source, modal: true) }
        }
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
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: "chevron.left")
                Text(parentTitle).multilineTextAlignment(.leading)
            }
            .frame(minWidth: 44, minHeight: 44, alignment: .leading)
            .contentShape(Rectangle())
        }
        .accessibilityLabel(title)
    }

    private var parentTitle: String {
        if title.hasPrefix("Back to ") { return String(title.dropFirst("Back to ".count)) }
        if title.hasPrefix("Return to ") { return String(title.dropFirst("Return to ".count)) }
        return title
    }
}

struct LibraryView: View {
    let content: BriefingContent
    var audioState = AudioPlaybackState()
    var onPlay: (AudioTrack) -> Void = { _ in }
    var onReaderVisible: ((AudioContentIdentity?) -> Void)? = nil
    var onOpenEdition: ((Edition) -> Void)? = nil
    var onOpenTopic: ((Topic) -> Void)? = nil
    var onSettings: (() -> Void)? = nil
    @State private var showingSaved = false
    @State private var savedItems: [SavedReaderItem] = []
    @ScaledMetric(relativeTo: .title) private var editionTitleSize: CGFloat = 21
    private var editions: [Edition] { content.currentEditions }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Picker("Library view", selection: $showingSaved) {
                    Text("Editions").tag(false)
                    Text("Saved").tag(true)
                }
                .pickerStyle(.segmented)
                .padding(.bottom, 24)
                if showingSaved {
                    Text("Saved on this device")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)
                        .padding(.bottom, 14)
                    if savedItems.isEmpty {
                        Text("Save an edition or explanation to find it here.")
                            .font(.body).foregroundStyle(ReaderColor.secondary)
                    } else {
                        ForEach(savedItems) { item in
                            Button {
                                if let edition = item.edition { onOpenEdition?(edition) }
                                else if let topic = item.topic { onOpenTopic?(topic) }
                            } label: {
                                HStack {
                                    savedRow(item)
                                    Spacer(minLength: 0)
                                    Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                                        .foregroundStyle(ReaderColor.secondary)
                                }
                                .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            Divider()
                        }
                    }
                } else if editions.isEmpty {
                    Text("Past editions will appear here.")
                        .font(.body).foregroundStyle(ReaderColor.secondary)
                } else {
                    ForEach(editions.sorted(by: { ($0.localDate ?? $0.weekStart ?? "") > ($1.localDate ?? $1.weekStart ?? "") })) { edition in
                        Button { onOpenEdition?(edition) } label: {
                            HStack(alignment: .top, spacing: 12) {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(edition.kind == "weekly" ? "WEEKLY RECAP" : "DAILY EDITION")
                                        .font(.caption.weight(.semibold)).tracking(0.8)
                                        .foregroundStyle(ReaderColor.secondary)
                                    Text(edition.localDate ?? edition.weekStart ?? edition.asOf ?? "Date unavailable")
                                        .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                                    Text(edition.title)
                                        .font(.system(size: editionTitleSize, weight: .semibold, design: .serif))
                                        .foregroundStyle(.primary)
                                        .accessibilityHeading(.h2)
                                    if edition.materialCorrection != nil {
                                        Text("Corrected")
                                            .font(.caption.weight(.semibold))
                                            .foregroundStyle(ReaderColor.correction)
                                    }
                                    Text("\(readingMinutes(edition)) min read")
                                        .font(.footnote).foregroundStyle(ReaderColor.secondary)
                                }
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                                    .foregroundStyle(ReaderColor.secondary)
                            }
                            .padding(.vertical, 18)
                            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        Divider()
                    }
                }
            }
            .padding(.horizontal, 20).padding(.top, 24).padding(.bottom, 36)
            .frame(maxWidth: 720, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Library")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let onSettings {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: onSettings) {
                        Image(systemName: "gearshape").frame(minWidth: 44, minHeight: 44)
                    }.accessibilityLabel("Settings")
                }
            }
        }
        .onAppear { onReaderVisible?(nil); loadSavedItems() }
        .onChange(of: content.currentEdition(kind: "daily")?.revisionID) { _, _ in loadSavedItems() }
        .onReceive(NotificationCenter.default.publisher(for: .briefingSavedItemsChanged)) { _ in loadSavedItems() }
    }

    private func savedRow(_ item: SavedReaderItem) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Text(item.subtitle.replacingOccurrences(of: " · Corrected", with: "")
                    .replacingOccurrences(of: " · Updated since saved", with: ""))
                    .font(.footnote.weight(.medium)).foregroundStyle(ReaderColor.secondary)
                if item.isCorrected {
                    Text("Corrected").font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.correction)
                }
                if item.isUpdated {
                    Text("Updated").font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.correction)
                }
            }
            Text(item.title).font(.body.weight(.semibold))
        }.padding(.vertical, 6).frame(minHeight: 44, alignment: .leading)
    }

    private func readingMinutes(_ edition: Edition) -> Int {
        edition.readingMinutes
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
