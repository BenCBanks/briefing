import SwiftUI

enum ReaderRoute: Hashable {
    case edition(String, String?)
    case explanation(String)
    case source(String)
}

struct TodayFrontPage: View {
    let content: BriefingContent
    let onOpenEdition: (Edition, String?) -> Void
    let onOpenWeekly: (Edition) -> Void
    var audioState = AudioPlaybackState()
    var onPlay: (AudioTrack) -> Void = { _ in }
    @State private var readingSectionID: String?

    private var daily: Edition? { content.currentEdition(kind: "daily") }
    private var weekly: Edition? { content.currentEdition(kind: "weekly") }
    private var dailySections: [ArticleSection] { daily?.readingSections ?? [] }
    private var quietCoverage: [Coverage] {
        (daily?.coverage ?? []).filter { $0.state == "quiet" || $0.state == "limited" }
    }

    var body: some View {
        ScrollView {
            if let daily {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 8) {
                        Text(displayDate(daily.localDate)).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                        Text("· Sample · fictional").font(.caption.weight(.medium)).foregroundStyle(ReaderColor.secondary)
                    }.padding(.bottom, 20)
                    Text(daily.title)
                        .font(.system(.largeTitle, design: .serif).weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader).padding(.bottom, 12)
                    Text("\(daily.readingMinutes) min read · \(dailySections.count) stories\(listenDuration)")
                        .font(.subheadline).foregroundStyle(ReaderColor.secondary).padding(.bottom, 20)
                    if daily.materialCorrection != nil {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("Audio paused: one story was corrected").font(.subheadline.weight(.semibold)).foregroundStyle(ReaderColor.correction)
                            Text("The corrected edition remains available to read.").font(.footnote).foregroundStyle(ReaderColor.secondary)
                        }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                            .background(ReaderColor.correction.opacity(0.10), in: RoundedRectangle(cornerRadius: 12)).padding(.bottom, 16)
                    }
                    Button {
                        onOpenEdition(daily, dailySections.first?.id)
                    } label: {
                        Label("Read today's briefing", systemImage: "arrow.right")
                            .font(.headline).frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(.borderedProminent).tint(ReaderColor.primaryAction).padding(.bottom, 8)
                    if let track = dailyTrack {
                        Button {
                            onPlay(track)
                        } label: {
                            Label(audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .playing ? "Pause briefing" : "Play briefing",
                                  systemImage: audioState.track?.contentIdentity == track.contentIdentity && audioState.status == .playing ? "pause.fill" : "play.fill")
                                .font(.subheadline.weight(.medium)).frame(maxWidth: .infinity, minHeight: 44)
                        }.buttonStyle(.plain).foregroundStyle(ReaderColor.action)
                    }
                    Text("IN THIS EDITION").font(.caption.weight(.semibold)).tracking(1.1)
                        .foregroundStyle(ReaderColor.secondary).padding(.top, 30).padding(.bottom, 8)
                    ForEach(dailySections.indices, id: \.self) { index in
                        let section = dailySections[index]
                        Button { onOpenEdition(daily, section.id) } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text(section.topicIDs?.compactMap { id in content.topics.first(where: { $0.id == id })?.label }.joined(separator: " · ") ?? "Story")
                                        .font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.secondary)
                                    Spacer()
                                    if daily.materialCorrection?.sectionIDs.contains(section.id) == true {
                                        Text("Corrected").font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.correction)
                                    }
                                }
                                Text(section.heading).font(.system(size: 20, weight: .semibold, design: .serif)).foregroundStyle(.primary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text(previewLine(section.body)).font(.subheadline).foregroundStyle(ReaderColor.secondary)
                                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                                Text("\(max(1, Int(ceil(Double(section.body.split(whereSeparator: \.isWhitespace).count) / 225)))) min read")
                                    .font(.caption).foregroundStyle(ReaderColor.secondary)
                            }.frame(maxWidth: .infinity, minHeight: 76, alignment: .leading).padding(.vertical, 14)
                        }.buttonStyle(.plain)
                        Divider().overlay(ReaderColor.separator)
                    }
                    if !quietCoverage.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("QUIET TODAY").font(.caption.weight(.semibold)).tracking(1).foregroundStyle(ReaderColor.secondary)
                            ForEach(quietCoverage) { item in
                                Text(content.topics.first(where: { $0.id == item.topicID })?.label ?? item.topicID)
                                    .font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }.padding(.top, 24)
                    }
                    if let weekly {
                        Text("THE WEEK IN PERSPECTIVE").font(.caption.weight(.semibold)).tracking(1)
                            .foregroundStyle(ReaderColor.secondary).padding(.top, 34).padding(.bottom, 8)
                        Button { onOpenWeekly(weekly) } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(weekly.title).font(.system(size: 21, weight: .semibold, design: .serif)).foregroundStyle(.primary)
                                Text("Weekly recap · \(weekly.readingMinutes) min read")
                                    .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                                Text("Next recap available Sunday").font(.caption).foregroundStyle(ReaderColor.secondary)
                            }.frame(maxWidth: .infinity, minHeight: 60, alignment: .leading).padding(.vertical, 12)
                        }.buttonStyle(.plain)
                    }
                }.padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 40)
                    .frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity)
            } else {
                ContentUnavailableView("No edition available", systemImage: "text.page",
                                       description: Text("The bundled sample has no daily edition."))
            }
        }
        .background(Color(uiColor: .systemBackground))
        .navigationTitle("Today").navigationBarTitleDisplayMode(.inline)
        .onAppear { refreshReadingPlace() }
    }

    private var listenDuration: String {
        guard dailyTrack != nil, let seconds = daily?.audio?.durationSeconds, seconds > 0 else { return "" }
        return " · \(max(1, Int((Double(seconds) / 60).rounded()))) min listen"
    }

    private var dailyTrack: AudioTrack? {
        // A material correction invalidates the whole edition recording, including untouched stories.
        guard daily?.materialCorrection == nil else { return nil }
        return daily?.audio?.track(id: daily?.id ?? "", title: daily?.title ?? "", kind: .dailyEdition,
                                   revisionID: daily?.revisionID, scriptHash: daily?.scriptHash)
    }

    private func previewLine(_ text: String) -> String {
        let first = text.split(separator: "\n", maxSplits: 1).first.map(String.init) ?? text
        let sentence = first.split(separator: ".", maxSplits: 1).first.map(String.init) ?? first
        return sentence.trimmingCharacters(in: .whitespacesAndNewlines) + (sentence.count < first.count ? "." : "")
    }

    private func refreshReadingPlace() {
        guard let daily else { readingSectionID = nil; return }
        let key = ReaderProgressKey.reading(itemID: daily.id,
                                            revisionID: daily.revisionID ?? "unversioned",
                                            scriptHash: daily.scriptHash)
        if let saved = UserDefaults.standard.string(forKey: key),
           daily.readingSections.contains(where: { $0.id == saved }) {
            readingSectionID = saved
        } else {
            readingSectionID = nil
        }
    }

    private func displayDate(_ raw: String?) -> String {
        guard let raw else { return "Today" }
        let input = DateFormatter()
        input.dateFormat = "yyyy-MM-dd"
        input.timeZone = TimeZone(secondsFromGMT: 0)
        guard let date = input.date(from: raw) else { return raw }
        let output = DateFormatter()
        output.dateFormat = "EEEE, MMMM d"
        return output.string(from: date)
    }
}
