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
                    Text(displayDate(daily.localDate))
                        .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                        .padding(.bottom, 24)
                    Text("SAMPLE · FICTIONAL CONTENT")
                        .font(.caption.weight(.semibold)).tracking(1.2)
                        .foregroundStyle(ReaderColor.secondary)
                        .padding(.bottom, 10)
                    Text(daily.title)
                        .font(.system(size: 34, weight: .semibold, design: .serif))
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                        .padding(.bottom, 12)
                    if let summary = daily.summary {
                        Text(summary)
                            .font(.system(size: 18)).lineSpacing(6)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.bottom, 14)
                    }
                    if let correction = daily.materialCorrection {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("CORRECTION")
                                .font(.caption.weight(.semibold)).tracking(1)
                                .foregroundStyle(ReaderColor.correction)
                            Text(correction.whatChanged)
                                .font(.subheadline)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.vertical, 12)
                        .padding(.horizontal, 14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(ReaderColor.correction.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                        .padding(.bottom, 16)
                    }
                    Text("About \(daily.readingMinutes) min read · \(dailySections.count) topics")
                        .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                        .padding(.bottom, 24)
                    Button {
                        onOpenEdition(daily, readingSectionID)
                    } label: {
                        HStack {
                            Text(readingSectionID == nil ? "Read today's briefing" : "Continue reading")
                            Spacer()
                            Image(systemName: "arrow.right")
                        }
                        .font(.headline)
                        .padding(.horizontal, 18)
                        .frame(minHeight: 52)
                        .foregroundStyle(.white)
                        .background(ReaderColor.action, in: RoundedRectangle(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)
                    if let readingSectionID,
                       let index = dailySections.firstIndex(where: { $0.id == readingSectionID }) {
                        Text("Section \(index + 1) of \(dailySections.count)")
                            .font(.footnote).foregroundStyle(ReaderColor.secondary)
                            .padding(.top, 8)
                    }

                    Text("IN THIS EDITION")
                        .font(.caption.weight(.semibold)).tracking(1.1)
                        .foregroundStyle(ReaderColor.secondary)
                        .padding(.top, 40).padding(.bottom, 10)
                    ForEach(dailySections.indices, id: \.self) { index in
                        let section = dailySections[index]
                        Button { onOpenEdition(daily, section.id) } label: {
                            HStack(alignment: .top, spacing: 14) {
                                Text(String(format: "%02d", index + 1))
                                    .font(.subheadline.monospacedDigit())
                                    .foregroundStyle(ReaderColor.secondary)
                                    .frame(width: 28, alignment: .leading)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(section.topicIDs?.compactMap { id in content.topics.first(where: { $0.id == id })?.label }.joined(separator: " · ") ?? "Story")
                                        .font(.caption.weight(.semibold)).foregroundStyle(ReaderColor.secondary)
                                    Text(section.heading)
                                        .font(.system(size: 19, weight: .semibold, design: .serif))
                                        .foregroundStyle(.primary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(ReaderColor.secondary)
                            }
                            .padding(.vertical, 16)
                            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        Divider()
                    }
                    if !quietCoverage.isEmpty {
                        DisclosureGroup("Other followed topics (\(quietCoverage.count))") {
                            ForEach(quietCoverage) { item in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(content.topics.first(where: { $0.id == item.topicID })?.label ?? item.topicID)
                                        .font(.subheadline.weight(.medium))
                                    Text(item.state == "limited" ? "Coverage limited" : "Quiet today")
                                        .font(.footnote).foregroundStyle(ReaderColor.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 6)
                            }
                        }
                        .font(.subheadline)
                        .padding(.top, 18)
                    }
                    if let weekly {
                        Text("THE WEEK IN PERSPECTIVE")
                            .font(.caption.weight(.semibold)).tracking(1.1)
                            .foregroundStyle(ReaderColor.secondary)
                            .padding(.top, 40).padding(.bottom, 10)
                        Button { onOpenWeekly(weekly) } label: {
                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(weekly.title)
                                        .font(.system(size: 21, weight: .semibold, design: .serif))
                                        .foregroundStyle(.primary)
                                    Text("Weekly recap · \(weekly.readingMinutes) min read")
                                        .font(.subheadline).foregroundStyle(ReaderColor.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(ReaderColor.secondary)
                            }
                            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 24)
                .padding(.bottom, 40)
                .frame(maxWidth: 720, alignment: .leading)
                .frame(maxWidth: .infinity)
            } else {
                ContentUnavailableView("No edition available", systemImage: "text.page",
                                       description: Text("The bundled sample has no daily edition."))
            }
        }
        .background(Color(uiColor: .systemBackground))
        .navigationTitle("Today")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { refreshReadingPlace() }
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
