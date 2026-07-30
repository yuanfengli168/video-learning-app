// bin/material_ocr
//
// MVP0.2 followup: macOS Vision OCR for image-only PDFs.
//
// Reads a PDF path + options from CLI args, renders each page via
// PDFKit, runs Apple's Vision framework (`VNRecognizeTextRequest`)
// on each rendered page, and emits JSON to stdout with the recognized
// text per page. This is the same OCR pipeline that macOS Preview
// uses for "Live Text" — runs on the Neural Engine, no model
// download, no third-party deps.
//
// USAGE:
//   material_ocr --pdf <path> [--max-pages N] [--langs chi_sim+eng]
//                 [--min-confidence 0.0]
//
// OUTPUT (stdout, single line of JSON):
//   {"ok": true, "page_count": 84, "pages": [
//     {"index": 0, "text": "...", "confidence": 0.92, "char_count": 1234},
//     ...
//   ]}
//
// ERRORS (stdout, JSON):
//   {"ok": false, "error": "..."}
//
// Build:
//   swiftc -O bin/material_ocr.swift -o bin/material_ocr -framework PDFKit -framework Vision -framework AppKit
//
// MVP0.2: this binary is invoked from app/services/material_extractor.py
// via subprocess. The Python side falls back to other OCR paths if
// the binary is missing or returns an error.

import Foundation
import Vision
import PDFKit
import AppKit

// MARK: - Helpers

struct OCRResult: Codable {
    let ok: Bool
    let pageCount: Int?
    let pages: [PageResult]?
    let error: String?

    struct PageResult: Codable {
        let index: Int
        let text: String
        let confidence: Double
        let charCount: Int
    }
}

struct OCRResultOk: Codable {
    let ok: Bool
    let pageCount: Int
    let pages: [OCRResult.PageResult]
}

struct OCRResultErr: Codable {
    let ok: Bool
    let error: String
}

func emitJSON(_ obj: Encodable) {
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? enc.encode(obj),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        // Last-ditch error — should never happen
        print("{\"ok\": false, \"error\": \"failed to encode OCR result JSON\"}")
    }
}

func emitError(_ msg: String) {
    emitJSON(OCRResultErr(ok: false, error: msg))
    exit(1)
}

// Render a PDFPage to PNG data via NSBitmapImageRep + Quartz
// (Coordinate system: PDFKit uses bottom-left origin; CGContext
// uses top-left; we flip Y via translateCTM + scaleCTM.)
func renderPageToPNG(_ page: PDFPage, scale: CGFloat) -> Data? {
    let bounds = page.bounds(for: .mediaBox)
    let pixelW = Int(bounds.width * scale)
    let pixelH = Int(bounds.height * scale)

    guard let bmp = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: pixelW,
        pixelsHigh: pixelH,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        return nil
    }

    NSGraphicsContext.saveGraphicsState()
    defer { NSGraphicsContext.restoreGraphicsState() }

    guard let ctx = NSGraphicsContext(bitmapImageRep: bmp) else { return nil }
    NSGraphicsContext.current = ctx
    let cg = ctx.cgContext

    // White background (Vision prefers white-on-dark or dark-on-white;
    // white is the safer default for documents)
    NSColor.white.set()
    NSBezierPath.fill(NSRect(x: 0, y: 0, width: pixelW, height: pixelH))

    // Flip Y so PDFKit (bottom-left origin) maps to CG (top-left)
    cg.translateBy(x: 0, y: CGFloat(pixelH))
    cg.scaleBy(x: 1.0, y: -1.0)
    page.draw(with: .mediaBox, to: cg)

    return bmp.representation(using: .png, properties: [:])
}

// Run Vision text recognition on PNG data. Synchronous; runs on
// the Neural Engine for sub-second recognition per page.
func recognizeText(in pngData: Data, languages: [String] = []) -> (String, Double) {
    let request = VNRecognizeTextRequest()
    let handler = VNImageRequestHandler(data: pngData, options: [:])

    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    if !languages.isEmpty {
        request.recognitionLanguages = languages
    }

    do {
        try handler.perform([request])
    } catch {
        return ("", 0.0)
    }

    guard let observations = request.results else { return ("", 0.0) }

    var lines: [String] = []
    var totalConf: Double = 0
    var count: Int = 0

    for obs in observations {
        guard let top = obs.topCandidates(1).first else { continue }
        lines.append(top.string)
        totalConf += Double(top.confidence)
        count += 1
    }

    let avgConf = count > 0 ? totalConf / Double(count) : 0.0
    return (lines.joined(separator: "\n"), avgConf)
}

// MARK: - Main

func parseArgs(_ args: [String]) -> (pdfPath: String, maxPages: Int?, langs: [String], minConf: Double) {
    var pdfPath: String?
    var maxPages: Int?
    var langs: [String] = ["en-US"]
    var minConf: Double = 0.0
    var i = 1
    while i < args.count {
        switch args[i] {
        case "--pdf":
            i += 1
            pdfPath = args[i]
        case "--max-pages":
            i += 1
            maxPages = Int(args[i])
        case "--langs":
            i += 1
            langs = args[i].split(separator: "+").map(String.init)
        case "--min-confidence":
            i += 1
            minConf = Double(args[i]) ?? 0.0
        default:
            break
        }
        i += 1
    }
    guard let path = pdfPath else {
        emitError("missing required --pdf <path>")
        exit(1)
    }
    return (path, maxPages, langs, minConf)
}

let parsed = parseArgs(CommandLine.arguments)
let args = parsed

let url = URL(fileURLWithPath: args.pdfPath)

guard let doc = PDFDocument(url: url) else {
    emitError("PDFDocument failed to open \(args.pdfPath)")
    exit(1)
}

let totalPages = doc.pageCount
let limit = args.maxPages ?? min(totalPages, 200)  // hard cap of 200 pages

let scale: CGFloat = 2.0  // 144 DPI; good for Vision recognition

var results: [OCRResult.PageResult] = []

for i in 0..<limit {
    guard let page = doc.page(at: i) else { continue }

    guard let pngData = renderPageToPNG(page, scale: scale) else {
        // Failed to render this page — skip it, don't fail the whole job
        continue
    }

    let (text, conf) = recognizeText(in: pngData, languages: args.langs)
    results.append(OCRResult.PageResult(
        index: i,
        text: text,
        confidence: conf,
        charCount: text.count
    ))
}

emitJSON(OCRResultOk(ok: true, pageCount: totalPages, pages: results))