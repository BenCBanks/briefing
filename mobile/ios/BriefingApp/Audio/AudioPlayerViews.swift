import SwiftUI

/// Compact player above the app's native tab bar. The title alone opens the expanded player.
public struct AudioMiniPlayer: View {
    @ObservedObject private var controller: AudioController
    @Binding private var isExpanded: Bool
    @AccessibilityFocusState private var playerTitleFocused: Bool
    private let focusRestoreToken: Int
    private let shouldRestoreFocus: Bool

    public init(controller: AudioController, isExpanded: Binding<Bool>, focusRestoreToken: Int = 0,
                shouldRestoreFocus: Bool = true) {
        self.controller = controller
        self._isExpanded = isExpanded
        self.focusRestoreToken = focusRestoreToken
        self.shouldRestoreFocus = shouldRestoreFocus
    }

    public var body: some View {
        Group {
            if let track = controller.state.track {
                HStack(spacing: 12) {
                    Button { isExpanded = true } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(track.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                            Text(statusText).font(.caption).foregroundStyle(ReaderColor.secondary).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens the expanded audio player")
                    .accessibilityFocused($playerTitleFocused)

                    if controller.state.status != .superseded {
                        Button(action: controller.togglePlayback) {
                            Image(systemName: controller.state.status.isPlaying ? "pause.fill" : "play.fill")
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(playbackActionLabel)
                        .disabled(controller.state.status == .missingResource || controller.state.status == .pending || controller.state.status == .loading || isFailed)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .overlay(alignment: .bottom) {
                    GeometryReader { proxy in
                        ZStack(alignment: .leading) {
                            Rectangle().fill(ReaderColor.separator)
                            Rectangle().fill(Color.accentColor)
                                .frame(width: proxy.size.width * controller.state.progress)
                        }
                    }
                    .frame(height: 2)
                }
                .background(.regularMaterial)
                .accessibilityElement(children: .contain)
                .onChange(of: focusRestoreToken) { _, _ in
                    if shouldRestoreFocus, controller.state.track != nil { playerTitleFocused = true }
                }
            }
        }
    }

    private var isFailed: Bool { if case .failed = controller.state.status { return true }; return false }
    private var playbackActionLabel: String {
        if controller.state.status.isPlaying { return "Pause audio" }
        if controller.state.duration > 0 && controller.state.position >= controller.state.duration { return "Replay audio" }
        return "Play audio"
    }
    private var statusText: String {
        let elapsed = AudioTime.format(controller.state.position)
        let duration = AudioTime.format(controller.state.duration)
        switch controller.state.status {
        case .pending: return "Audio pending approval"
        case .loading: return "Loading audio…"
        case .missingResource: return "Audio file not available"
        case .failed: return "Audio couldn't load · Retry in player"
        case .superseded:
            switch controller.state.track?.kind {
            case .explanation: return "Playback stopped · Current explanation available"
            case .dailyEdition, .weeklyEdition: return "Playback stopped · Updated edition available"
            default: return "Playback stopped"
            }
        default: return "\(elapsed) / \(duration)"
        }
    }
}

/// Expanded player. Hook `onReadTranscript` to the app's matching content revision.
public struct AudioExpandedPlayer: View {
    @ObservedObject private var controller: AudioController
    @Environment(\.dismiss) private var dismiss
    @State private var isShowingChapters = false
    @AccessibilityFocusState private var chaptersRowFocused: Bool
    #if DEBUG
    @State private var didAutoReadTranscript = false
    #endif
    private let onReadTranscript: (() -> Void)?
    private let transcriptTitle: String?

    public init(controller: AudioController, transcriptTitle: String? = nil,
                onReadTranscript: (() -> Void)? = nil) {
        self.controller = controller
        self.transcriptTitle = transcriptTitle
        self.onReadTranscript = onReadTranscript
    }

    public var body: some View {
        NavigationStack {
            ScrollView {
              VStack(spacing: 28) {
                VStack(spacing: 10) {
                    Text(controller.state.track?.title ?? "Audio player")
                        .font(.title2.weight(.semibold)).multilineTextAlignment(.center)
                    Text(statusMessage).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                }
                Slider(value: Binding(get: { controller.state.position }, set: { controller.seek(to: $0) }), in: 0...max(controller.state.duration, 1))
                    .accessibilityLabel("Audio position")
                    .disabled(controller.state.duration <= 0 || isLoading || isUnavailable || isPending || isSuperseded || isFailed)
                HStack { Text(AudioTime.format(controller.state.position)); Spacer(); Text("−\(AudioTime.format(max(0, controller.state.duration - controller.state.position)))") }
                    .font(.caption.monospacedDigit()).foregroundStyle(ReaderColor.secondary)
                    .padding(.top, -22)
                HStack(spacing: 30) {
                    Button { controller.skip(by: -15) } label: { Image(systemName: "gobackward.15").font(.title2).frame(width: 44, height: 44) }
                        .accessibilityLabel("Back 15 seconds")
                        .disabled(isUnavailable || isPending || isLoading || isSuperseded || isFailed)
                    Button(action: controller.togglePlayback) {
                        Image(systemName: controller.state.status.isPlaying ? "pause.fill" : "play.fill")
                            .font(.title2).frame(width: 64, height: 64)
                            .background(Color.accentColor, in: Circle()).foregroundStyle(.white)
                    }
                    .accessibilityLabel(expandedPlaybackActionLabel)
                    .disabled(isUnavailable || isPending || isLoading || isSuperseded || isFailed)
                    Button { controller.skip(by: 15) } label: { Image(systemName: "goforward.15").font(.title2).frame(width: 44, height: 44) }
                        .accessibilityLabel("Forward 15 seconds")
                        .disabled(isUnavailable || isPending || isLoading || isSuperseded || isFailed)
                }
                Picker("Playback speed", selection: Binding(get: { controller.state.rate }, set: controller.setRate)) {
                    ForEach(controller.rates, id: \.self) { rate in Text(rateLabel(rate)).tag(rate) }
                }
                .pickerStyle(.segmented)
                .frame(minHeight: 44)

                if controller.hasChapterNavigation {
                    NavigationLink(isActive: $isShowingChapters) {
                        AudioChaptersListView(controller: controller)
                    } label: {
                        HStack {
                            Text("Chapters")
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.secondary)
                        }
                        .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Chapters")
                    .accessibilityFocused($chaptersRowFocused)
                    .onChange(of: isShowingChapters) { _, showing in
                        if !showing { chaptersRowFocused = true }
                    }
                }

                if isLoading {
                    HStack { ProgressView(); Text("Loading audio…") }
                    Button("Cancel loading") { controller.cancelLoading(); dismiss() }.frame(minHeight: 44)
                } else if isUnavailable || isPending {
                    Text(isPending ? "Audio pending approval. The text is available." : "This recording isn't available. The text is still available.")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary).multilineTextAlignment(.center)
                    if isUnavailable {
                        Button("Check again") { controller.retry() }.frame(minHeight: 44)
                    }
                } else if isSuperseded {
                    Text(controller.state.track?.kind == .explanation
                         ? "This recording is out of date. The current explanation is available."
                         : "This recording is out of date. The current edition is available.")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary).multilineTextAlignment(.center)
                } else if case .failed = controller.state.status {
                    Text("Audio couldn't load")
                        .font(.footnote).foregroundStyle(ReaderColor.secondary)
                    Button("Retry") { controller.retry() }.frame(minHeight: 44)
                }

                if let onReadTranscript {
                    Button(action: onReadTranscript) {
                        Label(readTranscriptTitle, systemImage: "text.alignleft").frame(maxWidth: .infinity, minHeight: 44)
                    }
                }
              }
            }
            .padding(24)
            .navigationTitle("Now playing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Minimize") { dismiss() } } }
        }
        #if DEBUG
        .onAppear {
            openFixtureChaptersIfRequested()
            autoReadTranscriptIfRequested()
        }
        .onChange(of: controller.hasChapterNavigation) { _, _ in openFixtureChaptersIfRequested() }
        #endif
    }

    #if DEBUG
    private func openFixtureChaptersIfRequested() {
        guard CommandLine.arguments.contains("--briefing-chapters-open"),
              controller.hasChapterNavigation else { return }
        isShowingChapters = true
    }

    private func autoReadTranscriptIfRequested() {
        guard CommandLine.arguments.contains("--briefing-transcript-auto-read"),
              !didAutoReadTranscript, onReadTranscript != nil else { return }
        didAutoReadTranscript = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { onReadTranscript?() }
    }
    #endif

    private var isLoading: Bool { controller.state.status == .loading }
    private var isUnavailable: Bool { controller.state.status == .missingResource }
    private var isPending: Bool { controller.state.status == .pending }
    private var isSuperseded: Bool { controller.state.status == .superseded }
    private var isFailed: Bool { if case .failed = controller.state.status { return true }; return false }
    private var expandedPlaybackActionLabel: String {
        if controller.state.status.isPlaying { return "Pause" }
        if controller.state.duration > 0 && controller.state.position >= controller.state.duration { return "Replay" }
        return "Play"
    }
    private var readTranscriptTitle: String {
        if let transcriptTitle { return transcriptTitle }
        guard isSuperseded else { return "Read transcript" }
        return controller.state.track?.kind == .explanation ? "Read current explanation" : "Read current edition"
    }
    private func rateLabel(_ rate: Float) -> String { String(format: "%g×", Double(rate)) }
    private var statusMessage: String {
        switch controller.state.status {
        case .pending: return "Audio pending approval"
        case .missingResource: return "Audio file not available"
        case .superseded: return "Recording is out of date"
        case .failed: return "Audio couldn't load · Retry"
        case .loading: return "Loading audio…"
        default: return controller.chapters.count > 1
            ? (controller.state.currentChapter?.title ?? "Recorded briefing")
            : "Recorded briefing"
        }
    }
}

private struct AudioChaptersListView: View {
    @ObservedObject var controller: AudioController
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    if dynamicTypeSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: 12) {
                            Button("Back to player") { dismiss() }
                                .frame(minHeight: 44, alignment: .leading)
                            Text("Chapters").font(.title2.weight(.semibold))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.bottom, 16)
                    }
                    ForEach(controller.chapters) { chapter in
                        Button {
                            controller.seek(to: chapter) { succeeded in
                                if succeeded { dismiss() }
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(chapter.title)
                                    .font(.body.weight(isCurrent(chapter) ? .semibold : .regular))
                                    .multilineTextAlignment(.leading)
                                    .fixedSize(horizontal: false, vertical: true)
                                HStack(spacing: 8) {
                                    if isCurrent(chapter) {
                                        Label("Current", systemImage: "checkmark")
                                            .font(.caption.weight(.semibold))
                                            .foregroundStyle(Color.accentColor)
                                            .accessibilityHidden(true)
                                    }
                                    Text(chapterTime(chapter.startSeconds))
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(ReaderColor.secondary)
                                }
                            }
                            .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(accessibilityLabel(for: chapter))
                        .id(chapter.id)

                        if chapter.id != controller.chapters.last?.id {
                            Rectangle().fill(ReaderColor.separator).frame(height: 1).accessibilityHidden(true)
                        }
                    }
                }
            }
            .padding(.horizontal, 24)
            .onAppear {
                if let current = controller.state.currentChapter {
                    proxy.scrollTo(current.id, anchor: .center)
                }
            }
        }
        .navigationTitle(dynamicTypeSize.isAccessibilitySize ? "" : "Chapters")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden()
        .toolbar(dynamicTypeSize.isAccessibilitySize ? .hidden : .visible, for: .navigationBar)
        .toolbar {
            if !dynamicTypeSize.isAccessibilitySize {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Back to player") { dismiss() }.frame(minHeight: 44)
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if controller.chapterSeekFailed {
                Text("Couldn't seek to that chapter. Try again.")
                    .font(.footnote)
                    .foregroundStyle(ReaderColor.secondary)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .padding(.horizontal, 16)
                    .background(.regularMaterial)
            }
        }
    }

    private func isCurrent(_ chapter: AudioChapter) -> Bool {
        controller.state.currentChapter?.id == chapter.id
    }

    private func accessibilityLabel(for chapter: AudioChapter) -> String {
        let label = "\(chapter.title), starts at \(chapterTime(chapter.startSeconds))"
        return isCurrent(chapter) ? "\(label), Current chapter" : label
    }

    private func chapterTime(_ seconds: TimeInterval) -> String {
        let whole = max(0, Int(seconds.rounded(.down)))
        if whole >= 3_600 { return String(format: "%d:%02d:%02d", whole / 3_600, (whole % 3_600) / 60, whole % 60) }
        return String(format: "%d:%02d", whole / 60, whole % 60)
    }
}

private enum AudioTime {
    static func format(_ seconds: TimeInterval) -> String {
        guard seconds.isFinite && seconds >= 0 else { return "0:00" }
        let whole = Int(seconds.rounded(.down))
        return "\(whole / 60):\(String(format: "%02d", whole % 60))"
    }
}
