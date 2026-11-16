# Native iOS reader

The SwiftUI app uses the design-pass-008 navigation: Today is the front page, each daily story opens as its own page, sources and explanations are pushed pages, and the three root tabs are Today, Topics, and Library. SwiftUI's navigation back item names the parent screen; the tab bar is hidden on pushed content.

Daily recordings are available only when the bundled audio metadata identifies an approved recording for the current revision and script hash. A material correction suppresses the entire edition track on Today and every story page. Corrected text stays readable. Settings displays the narration voice as **Not yet chosen** until a choice is approved.

The app remains local and uses fictional bundled sample content. Reader preferences, saved items, and progress stay on this iPhone. Source details show the recorded support and limits; an original opens only when its source record has a URL.

CI uses debug-only launch arguments and OCR checks in `.github/workflows/ios.yml`. The native target can only be compiled and exercised on macOS with Xcode. The Linux worktree host can inspect Swift syntax and run the shared content validator, but cannot verify physical iPhone behavior.
