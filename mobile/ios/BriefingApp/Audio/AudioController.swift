import AVFoundation
import MediaPlayer
import SwiftUI
import UIKit

@MainActor
public final class AudioController: ObservableObject {
    @Published public private(set) var state = AudioPlaybackState()
    @Published public private(set) var chapterSeekFailed = false

    private let player = AVPlayer()
    private let defaults: UserDefaults
    private var timeObserver: Any?
    private var itemStatusObservation: NSKeyValueObservation?
    private var durationObservation: NSKeyValueObservation?
    private var timeControlObservation: NSKeyValueObservation?
    private var pendingRestorePosition: TimeInterval?
    private var isRestoringPosition = false
    private var shouldAutoplayWhenReady = false
    private var playbackRequested = false
    // AVPlayer seek completions can arrive after a newer seek or after playback intent changes.
    private var seekGeneration: UInt64 = 0
    private var userSeekInFlight = false
    private var lastPersistedAt: TimeInterval = 0
    private var interruptions: NSObjectProtocol?
    private var routeChanges: NSObjectProtocol?
    private var completionObserver: NSObjectProtocol?
    private var appLifecycle: [NSObjectProtocol] = []
    private var remoteCommandsInstalled = false
    private var remoteCommandTargets: [Any] = []

    private let supportedRates: [Float] = [0.75, 1, 1.25, 1.5, 2]
    private let playbackRateKey = "briefing.audio.playbackRate"
    private let selectedTrackIDKey = "briefing.audio.selectedTrackID"
    private let selectedTrackKindKey = "briefing.audio.selectedTrackKind"
    private let selectedTrackRevisionKey = "briefing.audio.selectedTrackRevision"
    private let selectedTrackHashKey = "briefing.audio.selectedTrackScriptHash"
    public var rates: [Float] { supportedRates }
    public var chapters: [AudioChapter] {
        guard state.status != .superseded,
              let track = state.track, state.duration.isFinite, state.duration > 0 else { return [] }
        return AudioChapter.validated(track.chapters, duration: state.duration) ?? []
    }
    public var hasChapterNavigation: Bool { chapters.count > 1 }

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if defaults.object(forKey: playbackRateKey) != nil {
            let savedRate = defaults.float(forKey: playbackRateKey)
            state.rate = supportedRates.contains(savedRate) ? savedRate : 1
        }
        player.automaticallyWaitsToMinimizeStalling = true
        timeControlObservation = player.observe(\.timeControlStatus, options: [.initial, .new]) { [weak self] observedPlayer, _ in
            let status = observedPlayer.timeControlStatus
            Task { @MainActor in self?.handleTimeControlStatus(status) }
        }
        installObservers()
        installRemoteCommands()
        installTimeObserver()
    }

    deinit {
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        let center = NotificationCenter.default
        [interruptions, routeChanges, completionObserver].compactMap { $0 }.forEach(center.removeObserver)
        appLifecycle.forEach(center.removeObserver)
        let commands = MPRemoteCommandCenter.shared()
        remoteCommandTargets.forEach { target in
            commands.playCommand.removeTarget(target)
            commands.pauseCommand.removeTarget(target)
            commands.togglePlayPauseCommand.removeTarget(target)
            commands.changePlaybackPositionCommand.removeTarget(target)
            commands.skipBackwardCommand.removeTarget(target)
            commands.skipForwardCommand.removeTarget(target)
        }
    }

    public func select(_ track: AudioTrack) {
        if state.track == track {
            if case .failed = state.status { retry() }
            else if state.status == .missingResource { retry() }
            else { togglePlayback() }
            return
        }
        persistPosition()
        player.pause()
        player.replaceCurrentItem(with: nil)
        itemStatusObservation = nil
        durationObservation = nil
        pendingRestorePosition = nil
        isRestoringPosition = false
        shouldAutoplayWhenReady = false
        playbackRequested = false
        userSeekInFlight = false
        let playbackRate = state.rate
        state = AudioPlaybackState(track: track)
        state.rate = playbackRate
        chapterSeekFailed = false
        refreshCurrentChapter()
        rememberSelection(track)
        lastPersistedAt = 0
        guard let filename = track.resourceFilename else {
            state.status = .pending
            updateNowPlaying()
            return
        }
        guard let url = Bundle.main.url(forResource: filename, withExtension: nil) else {
            state.status = .missingResource
            persistPosition()
            updateNowPlaying()
            return
        }
        let saved = storedPosition(for: track)
        state.position = saved
        beginLoading(url: url, autoplay: true, restoring: saved)
        updateNowPlaying()
    }

    /// Restores a previously selected recording without starting playback. Used when the
    /// app relaunches and by simulator playback tests with a generated fixture URL.
    public func restore(_ track: AudioTrack, from resourceURL: URL? = nil) {
        player.pause()
        player.replaceCurrentItem(with: nil)
        itemStatusObservation = nil
        durationObservation = nil
        pendingRestorePosition = nil
        isRestoringPosition = false
        shouldAutoplayWhenReady = false
        playbackRequested = false
        userSeekInFlight = false
        let playbackRate = state.rate
        state = AudioPlaybackState(track: track)
        state.rate = playbackRate
        chapterSeekFailed = false
        refreshCurrentChapter()
        rememberSelection(track)
        let url: URL?
        if let resourceURL {
            url = resourceURL
        } else if let filename = track.resourceFilename {
            url = Bundle.main.url(forResource: filename, withExtension: nil)
        } else {
            url = nil
        }
        guard let url else {
            state.status = track.resourceFilename == nil ? .pending : .missingResource
            return
        }
        let saved = storedPosition(for: track)
        state.position = saved
        beginLoading(url: url, autoplay: false, restoring: saved)
        updateNowPlaying()
    }

    /// Finds the last selected recording in the current content pack and restores it paused.
    public func restoreLastSelectedTrack(from candidates: [AudioTrack], resourceURL: URL? = nil) {
        guard let id = defaults.string(forKey: selectedTrackIDKey),
              let kindRawValue = defaults.string(forKey: selectedTrackKindKey),
              let kind = AudioContentKind(rawValue: kindRawValue),
              let revision = defaults.string(forKey: selectedTrackRevisionKey),
              let scriptHash = defaults.string(forKey: selectedTrackHashKey),
              let track = candidates.first(where: {
                  $0.id == id && $0.kind == kind && $0.revision == revision && $0.scriptHash == scriptHash
              }) else { return }
        restore(track, from: resourceURL)
    }

    public func play() {
        if state.status == .pending || state.status == .missingResource || state.status == .superseded { return }
        if case .failed = state.status { return }
        guard player.currentItem != nil else {
            return
        }
        if userSeekInFlight {
            playbackRequested = true
            return
        }
        if isRestoringPosition {
            shouldAutoplayWhenReady = true
            return
        }
        if player.currentItem?.status != .readyToPlay {
            shouldAutoplayWhenReady = true
            state.status = .loading
            return
        }
        if pendingRestorePosition != nil {
            shouldAutoplayWhenReady = true
            return
        }
        if state.duration > 0, state.position >= state.duration {
            guard let item = player.currentItem else { return }
            playbackRequested = true
            seekGeneration &+= 1
            let generation = seekGeneration
            userSeekInFlight = true
            player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
                Task { @MainActor in
                    guard let self, self.player.currentItem === item,
                          self.seekGeneration == generation else { return }
                    self.userSeekInFlight = false
                    guard finished, self.playbackRequested else { return }
                    self.state.position = 0
                    self.refreshCurrentChapter()
                    self.startPlayback()
                }
            }
            return
        }
        startPlayback()
    }

    private func startPlayback() {
        guard player.currentItem != nil else { return }
        guard configureAudioSession() else { return }
        shouldAutoplayWhenReady = false
        playbackRequested = true
        player.playImmediately(atRate: state.rate)
        state.status = .ready
        updateNowPlaying()
    }

    public func pause() {
        guard player.currentItem != nil else { return }
        shouldAutoplayWhenReady = false
        playbackRequested = false
        let preserveRestoredPosition = isRestoringPosition || pendingRestorePosition != nil || state.status == .loading
        player.pause()
        state.status = .paused
        if !preserveRestoredPosition { persistPosition() }
        updateNowPlaying()
    }

    public func togglePlayback() {
        if state.status == .playing || player.timeControlStatus == .playing || playbackRequested {
            pause()
        } else if state.status == .loading {
            shouldAutoplayWhenReady = false
            player.pause()
            state.status = .paused
        } else {
            play()
        }
    }

    public func replay() {
        guard let item = player.currentItem else { retry(); return }
        playbackRequested = true
        seekGeneration &+= 1
        let generation = seekGeneration
        userSeekInFlight = true
        player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
            Task { @MainActor in
                guard let self, self.player.currentItem === item,
                      self.seekGeneration == generation else { return }
                self.userSeekInFlight = false
                guard finished, self.playbackRequested else { return }
                self.state.position = 0
                self.refreshCurrentChapter()
                self.persistPosition()
                self.startPlayback()
            }
        }
    }

    public func retry() {
        guard let track = state.track else { return }
        state.status = .idle
        selectForRetry(track)
    }

    public func seek(to seconds: TimeInterval, completion: ((Bool) -> Void)? = nil) {
        guard let item = player.currentItem else {
            if completion != nil { chapterSeekFailed = true }
            completion?(false)
            return
        }
        let upper = state.duration > 0 ? state.duration : max(seconds, 0)
        let bounded = min(max(seconds.isFinite ? seconds : 0, 0), upper)
        if completion != nil { chapterSeekFailed = false }
        seekGeneration &+= 1
        let generation = seekGeneration
        userSeekInFlight = true
        player.seek(to: CMTime(seconds: bounded, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
            Task { @MainActor in
                guard let self else { completion?(false); return }
                guard self.player.currentItem === item,
                      self.seekGeneration == generation else {
                    completion?(false)
                    return
                }
                self.userSeekInFlight = false
                guard finished else {
                    if completion != nil { self.chapterSeekFailed = true }
                    completion?(false)
                    return
                }
                self.state.position = bounded
                // Consult current intent here: Pause or an interruption may have happened
                // while AVPlayer was completing the seek.
                if self.playbackRequested { self.startPlayback() }
                else if self.state.status != .loading { self.player.pause(); self.state.status = .paused }
                self.persistPosition()
                self.refreshCurrentChapter()
                self.updateNowPlaying()
                completion?(true)
            }
        }
    }

    /// Seeks to a validated chapter without changing the selected recording or reading state.
    public func seek(to chapter: AudioChapter, completion: ((Bool) -> Void)? = nil) {
        guard let track = state.track,
              let chapters = AudioChapter.validated(track.chapters, duration: state.duration),
              chapters.count > 1,
              chapters.contains(chapter) else {
            chapterSeekFailed = true
            completion?(false)
            return
        }
        seek(to: chapter.startSeconds, completion: completion)
    }

    public func skip(by seconds: TimeInterval) { seek(to: state.position + seconds) }

    public func setRate(_ rate: Float) {
        guard supportedRates.contains(rate) else { return }
        state.rate = rate
        defaults.set(rate, forKey: playbackRateKey)
        if player.timeControlStatus == .playing { player.rate = rate }
        updateNowPlaying()
    }

    /// Call from the app's scenePhase handler; saves on background/inactive transitions.
    public func appWillResignActive() { persistPosition() }

    /// Cancels an in-flight load and removes the mini-player selection.
    public func cancelLoading() {
        guard state.status == .loading else { return }
        player.pause()
        player.replaceCurrentItem(with: nil)
        itemStatusObservation = nil
        durationObservation = nil
        pendingRestorePosition = nil
        isRestoringPosition = false
        shouldAutoplayWhenReady = false
        playbackRequested = false
        state = AudioPlaybackState()
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    /// Call when a material correction withdraws/replaces the selected audio revision.
    public func invalidateCurrentTrack() {
        guard player.currentItem != nil else { return }
        persistPosition()
        player.pause()
        player.replaceCurrentItem(with: nil)
        itemStatusObservation = nil
        durationObservation = nil
        pendingRestorePosition = nil
        isRestoringPosition = false
        shouldAutoplayWhenReady = false
        playbackRequested = false
        state.status = .superseded
        state.currentChapter = nil
        defaults.removeObject(forKey: selectedTrackIDKey)
        defaults.removeObject(forKey: selectedTrackKindKey)
        defaults.removeObject(forKey: selectedTrackRevisionKey)
        defaults.removeObject(forKey: selectedTrackHashKey)
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    private func selectForRetry(_ track: AudioTrack) {
        // Retry starts at the saved position and always starts paused until the user requests playback.
        let position = storedPosition(for: track)
        player.pause()
        player.replaceCurrentItem(with: nil)
        itemStatusObservation = nil
        durationObservation = nil
        guard let filename = track.resourceFilename else {
            state.status = .pending
            updateNowPlaying()
            return
        }
        guard let url = Bundle.main.url(forResource: filename, withExtension: nil) else {
            state.status = .missingResource
            updateNowPlaying()
            return
        }
        state.position = position
        beginLoading(url: url, autoplay: false, restoring: position)
    }

    private func beginLoading(url: URL, autoplay: Bool, restoring position: TimeInterval) {
        player.pause()
        let item = AVPlayerItem(url: url)
        player.replaceCurrentItem(with: item)
        state.status = .loading
        shouldAutoplayWhenReady = autoplay
        pendingRestorePosition = position > 0 ? position : nil
        isRestoringPosition = false
        itemStatusObservation = item.observe(\.status, options: [.initial, .new]) { [weak self] observedItem, _ in
            Task { @MainActor in self?.handleItemStatus(observedItem) }
        }
        durationObservation = item.observe(\.duration, options: [.initial, .new]) { [weak self] observedItem, _ in
            Task { @MainActor in
                guard let self, self.player.currentItem === observedItem else { return }
                let duration = CMTimeGetSeconds(observedItem.duration)
                if duration.isFinite && duration > 0 {
                    self.state.duration = duration
                    self.refreshCurrentChapter()
                }
                self.updateNowPlaying()
            }
        }
    }

    private func handleItemStatus(_ item: AVPlayerItem) {
        guard player.currentItem === item else { return }
        switch item.status {
        case .readyToPlay:
            let seconds = CMTimeGetSeconds(item.duration)
            if seconds.isFinite && seconds > 0 {
                state.duration = seconds
                refreshCurrentChapter()
            }
            let restore = pendingRestorePosition
            pendingRestorePosition = nil
            if let restore {
                let position = state.duration > 0 ? min(restore, state.duration) : restore
                isRestoringPosition = true
                player.seek(to: CMTime(seconds: position, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
                    Task { @MainActor in
                        guard let self, self.player.currentItem === item else { return }
                        self.isRestoringPosition = false
                        guard finished else { return }
                        self.state.position = position
                        self.refreshCurrentChapter()
                        if self.shouldAutoplayWhenReady, self.state.duration > 0, position >= self.state.duration {
                            self.player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] didReset in
                                Task { @MainActor in
                                    guard let self, didReset, self.player.currentItem === item, self.shouldAutoplayWhenReady else { return }
                                    self.state.position = 0
                                    self.startPlayback()
                                }
                            }
                        } else if self.shouldAutoplayWhenReady { self.startPlayback() }
                        else if self.state.status == .loading { self.state.status = .ready }
                        self.updateNowPlaying()
                    }
                }
            } else if shouldAutoplayWhenReady {
                startPlayback()
            } else if state.status == .loading {
                state.status = .ready
            }
        case .failed:
            shouldAutoplayWhenReady = false
            pendingRestorePosition = nil
            isRestoringPosition = false
            playbackRequested = false
            state.status = .failed(item.error?.localizedDescription ?? "Audio couldn't load")
            player.pause()
        case .unknown: break
        @unknown default: break
        }
        updateNowPlaying()
    }

    private func handleTimeControlStatus(_ status: AVPlayer.TimeControlStatus) {
        guard player.currentItem != nil else { return }
        guard player.timeControlStatus == status else { return }
        switch status {
        case .playing:
            guard player.currentItem?.status == .readyToPlay else { return }
            playbackRequested = true
            state.status = .playing
            updateNowPlaying()
        case .waitingToPlayAtSpecifiedRate:
            if state.status == .playing { state.status = .ready }
        case .paused:
            if state.status == .playing && !userSeekInFlight {
                playbackRequested = false
                state.status = .paused
                updateNowPlaying()
            }
        @unknown default: break
        }
    }

    @discardableResult
    private func configureAudioSession() -> Bool {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio, options: [.allowAirPlay])
            try session.setActive(true)
            return true
        } catch {
            state.status = .failed("Audio session unavailable")
            return false
        }
    }

    private func installObservers() {
        let center = NotificationCenter.default
        interruptions = center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            if type == .began { Task { @MainActor in self?.pause() } }
        }
        routeChanges = center.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable else { return }
            Task { @MainActor in self?.pause() }
        }
        for name in [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification] {
            appLifecycle.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.persistPosition() }
            })
        }
        completionObserver = center.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: nil, queue: .main) { [weak self] note in
            guard let item = note.object as? AVPlayerItem else { return }
            Task { @MainActor in self?.handleItemDidFinish(item) }
        }
    }

    private func installTimeObserver() {
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main) { [weak self] time in
            Task { @MainActor in
                guard let self else { return }
                let seconds = CMTimeGetSeconds(time)
                if seconds.isFinite {
                    self.state.position = seconds
                    if !self.userSeekInFlight { self.refreshCurrentChapter() }
                }
                if self.state.status == .playing && seconds - self.lastPersistedAt >= 5 {
                    self.persistPosition()
                    self.lastPersistedAt = seconds
                }
                self.updateNowPlaying()
            }
        }
    }

    private func installRemoteCommands() {
        guard !remoteCommandsInstalled else { return }
        remoteCommandsInstalled = true
        let center = MPRemoteCommandCenter.shared()
        remoteCommandTargets.append(center.playCommand.addTarget { [weak self] _ in Task { @MainActor in self?.play() }; return .success })
        remoteCommandTargets.append(center.pauseCommand.addTarget { [weak self] _ in Task { @MainActor in self?.pause() }; return .success })
        remoteCommandTargets.append(center.togglePlayPauseCommand.addTarget { [weak self] _ in Task { @MainActor in self?.togglePlayback() }; return .success })
        remoteCommandTargets.append(center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            Task { @MainActor in self?.seek(to: event.positionTime) }
            return .success
        })
        center.skipBackwardCommand.preferredIntervals = [15]
        center.skipForwardCommand.preferredIntervals = [15]
        remoteCommandTargets.append(center.skipBackwardCommand.addTarget { [weak self] _ in Task { @MainActor in self?.skip(by: -15) }; return .success })
        remoteCommandTargets.append(center.skipForwardCommand.addTarget { [weak self] _ in Task { @MainActor in self?.skip(by: 15) }; return .success })
    }

    private func updateNowPlaying() {
        guard state.status != .superseded,
              let track = state.track else { MPNowPlayingInfoCenter.default().nowPlayingInfo = nil; return }
        var info: [String: Any] = [MPMediaItemPropertyTitle: track.title,
                                   MPMediaItemPropertyAlbumTitle: "Briefing",
                                   MPNowPlayingInfoPropertyElapsedPlaybackTime: state.position,
                                   MPNowPlayingInfoPropertyPlaybackRate: state.status == .playing ? state.rate : 0,
                                   MPNowPlayingInfoPropertyDefaultPlaybackRate: state.rate]
        if state.duration > 0 { info[MPMediaItemPropertyPlaybackDuration] = state.duration }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func refreshCurrentChapter() {
        guard let track = state.track, state.duration.isFinite, state.duration > 0,
              let chapters = AudioChapter.validated(track.chapters, duration: state.duration),
              chapters.count > 1 else {
            state.currentChapter = nil
            return
        }
        if state.position >= state.duration {
            state.currentChapter = chapters.last
        } else {
            state.currentChapter = chapters.last(where: { $0.startSeconds <= state.position })
        }
    }

    private func persistPosition() {
        guard state.status != .superseded, player.currentItem != nil,
              let track = state.track else { return }
        if pendingRestorePosition != nil || isRestoringPosition {
            defaults.set(state.position, forKey: positionKey(track))
            return
        }
        let actual = CMTimeGetSeconds(player.currentTime())
        if actual.isFinite && actual >= 0 { state.position = actual }
        defaults.set(state.position, forKey: positionKey(track))
    }
    private func storedPosition(for track: AudioTrack) -> TimeInterval {
        defaults.double(forKey: positionKey(track))
    }
    private func positionKey(_ track: AudioTrack) -> String {
        "briefing.audio.\(track.kind.rawValue).\(track.id).\(track.revision).\(track.scriptHash)"
    }
    private func rememberSelection(_ track: AudioTrack) {
        defaults.set(track.id, forKey: selectedTrackIDKey)
        defaults.set(track.kind.rawValue, forKey: selectedTrackKindKey)
        defaults.set(track.revision, forKey: selectedTrackRevisionKey)
        defaults.set(track.scriptHash, forKey: selectedTrackHashKey)
    }

    private func handleItemDidFinish(_ item: AVPlayerItem) {
        guard player.currentItem === item else { return }
        player.pause()
        playbackRequested = false
        state.position = state.duration
        refreshCurrentChapter()
        state.status = .paused
        persistPosition()
        updateNowPlaying()
    }
}
