import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count >= 3 else {
    fputs("Usage: swift check_capture.swift image.png expected-text [expected-text ...]\n", stderr)
    exit(2)
}

let screenshot = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
try VNImageRequestHandler(url: screenshot).perform([request])
let recognized = (request.results ?? [])
    .compactMap { $0.topCandidates(1).first?.string }
    .joined(separator: " ")
func normalizedOCRText(_ text: String) -> String {
    String(text.filter { !$0.isWhitespace }).lowercased()
}
let normalizedRecognized = normalizedOCRText(recognized)

let arguments = Array(CommandLine.arguments.dropFirst(2))
for expected in arguments.filter({ !$0.hasPrefix("--") }) {
    guard normalizedRecognized.contains(normalizedOCRText(expected)) else {
        fputs("Missing '\(expected)' in \(screenshot.lastPathComponent). OCR: \(recognized)\n", stderr)
        exit(1)
    }
}

if arguments.contains("--dark-sheet") || arguments.contains("--light-sheet") {
    guard let imageData = NSImage(contentsOf: screenshot)?.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: imageData) else {
        fputs("Cannot inspect surface color in \(screenshot.lastPathComponent)\n", stderr)
        exit(1)
    }
    // Sample both page margins at several heights. The center of a reader can
    // land on white article text even when the entire page is correctly dark.
    let brightnessSamples = [0.02, 0.98].flatMap { x in
        [0.40, 0.70, 0.90].compactMap { y -> CGFloat? in
            guard let color = bitmap.colorAt(x: Int(Double(bitmap.pixelsWide) * x),
                                             y: Int(Double(bitmap.pixelsHigh) * y))?.usingColorSpace(.deviceRGB) else { return nil }
            return (color.redComponent + color.greenComponent + color.blueComponent) / 3
        }
    }.sorted()
    guard brightnessSamples.count >= 4 else {
        fputs("Cannot sample surface margins in \(screenshot.lastPathComponent)\n", stderr)
        exit(1)
    }
    let brightness = brightnessSamples[brightnessSamples.count / 2]
    if arguments.contains("--dark-sheet") && brightness > 0.35 {
        fputs("Expected dark surface in \(screenshot.lastPathComponent), sampled brightness \(brightness)\n", stderr)
        exit(1)
    }
    if arguments.contains("--light-sheet") && brightness < 0.65 {
        fputs("Expected light surface in \(screenshot.lastPathComponent), sampled brightness \(brightness)\n", stderr)
        exit(1)
    }
}
print("Verified \(screenshot.lastPathComponent): \(arguments.joined(separator: ", "))")
