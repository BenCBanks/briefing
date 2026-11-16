# iOS preview app

This is a standalone SwiftUI app generated from `project.yml` with XcodeGen. It targets iOS 17 and bundles the fictional JSON sample pack from the repository's `content/` directory. It has no runtime dependency on the browser app, a server, credentials, or another project.

## Build locally on macOS

Requirements: macOS with Xcode 15 or later, Xcode command line tools, and XcodeGen 2.38 or later.

```sh
cd mobile/ios
xcodegen generate
xcodebuild -project BriefingPreview.xcodeproj -scheme BriefingApp \
  -destination 'generic/platform=iOS Simulator' -derivedDataPath build build
```

The `BriefingAppTests` target checks complete topic coverage, honest pending-audio state, chapter boundaries, and real `AVPlayer` progress, Pause/Resume, seeking and restored position. The correction test checks that active playback stops and old listening seconds do not carry into a replacement revision. CI generates a 20-second technical WAV immediately before XcodeGen, bundles it only in the test target, and verifies it is absent from the ordinary app. CI copies that WAV and the fictional correction pack into the simulator app's Documents directory for debug-only captures of Today, Chapters, the explanation, Library, source details and a stopped recording. These images show controls, not a professional voice. Run the tests on an installed iPhone simulator; the Linux preview host cannot compile or run SwiftUI tests.

No Apple account is needed for a simulator build. To install on a device or distribute through TestFlight, create an App Store Connect app record, choose and confirm the final bundle identifier, set the matching Team and signing settings in Xcode, and use a valid Apple Development/Distribution certificate and provisioning profile. The current `com.bencbanks.briefing.prototype` identifier is provisional and must be confirmed/registered before signing. No credentials or signing assets are included here.

## Audio

The app uses the native `AudioController`, mini-player and expanded player. The XcodeGen target bundles the four JSON files individually. When a recording is approved, add its exact file path to `project.yml` resources; the file must be copied into the app bundle under its filename. Set `audio.state` to `recorded` only after the file, script revision and SHA-256 script hash match. `resource_filename` or a relative `/content/audio/…` URL identifies that bundled file. Pending audio has no Play control or speech-synthesis fallback. Background audio mode is enabled for the AVPlayer playback session.
