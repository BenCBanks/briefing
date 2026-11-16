import SwiftUI

/// Stage A preferences stay on this device. The bundled fictional edition is fixed.
struct PreferencesView: View {
    let topics: [Topic]
    @Environment(\.dismiss) private var dismiss
    @AppStorage("briefing.preferences.topicIDs") private var storedTopicIDs = ""
    @AppStorage("briefing.preferences.dailyMinutes") private var dailyMinutes = 12
    @AppStorage("briefing.preferences.timeZone") private var storedTimeZone = ""
    @State private var timeZoneDraft = ""
    @State private var timeZoneError = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Narration") {
                    LabeledContent("Voice", value: "Not yet chosen")
                    Text("Playback appears only after a recording is approved.")
                        .font(.footnote).foregroundStyle(.secondary)
                }

                Section {
                    Text("Choose one to five subjects for future editions. This fictional sample is fixed so its text and eventual recording stay in sync.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    ForEach(topics) { topic in
                        Toggle(isOn: binding(for: topic.id)) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(topic.label)
                                Text(topic.scope).font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                        .disabled(selectedIDs.count == 1 && selectedIDs.contains(topic.id))
                    }
                } header: { Text("Topics") }

                Section("Daily time budget") {
                    Picker("Minutes", selection: $dailyMinutes) {
                        Text("10 minutes").tag(10)
                        Text("12 minutes").tag(12)
                        Text("15 minutes").tag(15)
                    }
                    .pickerStyle(.segmented)
                }

                Section("Time zone") {
                    TextField("America/New_York", text: $timeZoneDraft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityLabel("IANA time zone")
                    if timeZoneError {
                        Text("Enter a time zone such as America/New_York.")
                            .font(.footnote).foregroundStyle(.red)
                    }
                    Button("Save time zone") { saveTimeZone() }
                        .frame(minHeight: 44)
                }

                Section {
                    Text("Choices, saves, and reading and listening progress stay on this iPhone. They do not sync with the browser or another device.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.frame(minWidth: 44, minHeight: 44)
                }
            }
            .onAppear {
                timeZoneDraft = storedTimeZone.isEmpty ? TimeZone.current.identifier : storedTimeZone
            }
        }
    }

    private var selectedIDs: Set<String> {
        let validIDs = Set(topics.map(\.id))
        let stored = Set(storedTopicIDs.split(separator: ",").map(String.init)).intersection(validIDs)
        return stored.isEmpty ? validIDs : stored
    }

    private func binding(for id: String) -> Binding<Bool> {
        Binding(
            get: { selectedIDs.contains(id) },
            set: { enabled in
                var next = selectedIDs
                if enabled { next.insert(id) }
                else if next.count > 1 { next.remove(id) }
                storedTopicIDs = topics.map(\.id).filter(next.contains).joined(separator: ",")
            }
        )
    }

    private func saveTimeZone() {
        let candidate = timeZoneDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard TimeZone(identifier: candidate) != nil else {
            timeZoneError = true
            return
        }
        storedTimeZone = candidate
        timeZoneError = false
    }
}
