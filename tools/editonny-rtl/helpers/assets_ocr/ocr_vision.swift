// Apple Vision text reader for screen_text.py.
// Usage: ocr_vision <folder with f_*.jpg> <output.json>
// Output: { "f_00001.jpg": [ {"t": "...", "c": 0.93, "x": .., "y": .., "w": .., "h": ..}, ... ] }
// x/y/w/h are fractions of the frame, with y measured from the bottom (the Vision convention).
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: ocr_vision <folder> <output.json>\n".data(using: .utf8)!)
    exit(2)
}
let folder = args[1], output = args[2]
let fm = FileManager.default
guard let names = try? fm.contentsOfDirectory(atPath: folder).filter({ $0.hasSuffix(".jpg") }).sorted() else {
    FileHandle.standardError.write("unreadable folder\n".data(using: .utf8)!)
    exit(2)
}
var out: [String: [[String: Any]]] = [:]
for name in names {
    let url = URL(fileURLWithPath: folder + "/" + name)
    guard let img = NSImage(contentsOf: url),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate          // .fast misses short words ("EXIT")
    req.usesLanguageCorrection = false
    if #available(macOS 13.0, *) {
        req.automaticallyDetectsLanguage = true
    }
    req.recognitionLanguages = ["pt-BR", "en-US", "ar-SA", "ar"]
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var items: [[String: Any]] = []
    for r in (req.results ?? []) {
        guard let c = r.topCandidates(1).first else { continue }
        let b = r.boundingBox
        items.append(["t": c.string, "c": c.confidence,
                      "x": b.minX, "y": b.minY, "w": b.width, "h": b.height])
    }
    out[name] = items
}
let data = try! JSONSerialization.data(withJSONObject: out)
try! data.write(to: URL(fileURLWithPath: output))
print("ok \(names.count)")
