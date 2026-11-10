import Foundation
import AVFoundation
import CoreMediaIO
import CoreImage
import AppKit

// Only USB muxed screen devices are eligible. No camera or remote control.
final class Receiver: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let lock = NSLock()
    private var buffer: CVPixelBuffer?
    private var sequence = 0
    private var capturedAt = Date()
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let frame = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lock.lock()
        buffer = frame
        sequence += 1
        capturedAt = Date()
        lock.unlock()
    }
    func latest() -> (CVPixelBuffer?, Int, Date) {
        lock.lock()
        defer { lock.unlock() }
        return (buffer, sequence, capturedAt)
    }
}

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
        print(String(decoding: data, as: UTF8.self))
        fflush(stdout)
    }
}

var directory = "session"
var deviceName: String?
var interval = 0.2
var duration = 86400.0
var listOnly = false
var i = 1
while i < CommandLine.arguments.count {
    let arg = CommandLine.arguments[i]
    if arg == "--list" { listOnly = true; i += 1; continue }
    guard i + 1 < CommandLine.arguments.count else { emit(["status": "error", "message": "Missing argument"]); exit(1) }
    let value = CommandLine.arguments[i+1]
    switch arg {
    case "--output": directory = value
    case "--device": deviceName = value
    case "--interval": interval = max(0.1, Double(value) ?? 0.2)
    case "--duration": duration = max(1, Double(value) ?? 86400)
    default: emit(["status": "error", "message": "Unknown option: \(arg)"]); exit(1)
    }
    i += 2
}
let dir = URL(fileURLWithPath: directory)
try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
func status(_ value: [String: Any]) {
    var object = value
    object["updated_at"] = Date().timeIntervalSince1970
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
        try? data.write(to: dir.appendingPathComponent("capture.json"), options: .atomic)
    }
}
var enabled: UInt32 = 1
var address = CMIOObjectPropertyAddress(
    mSelector: UInt32(kCMIOHardwarePropertyAllowScreenCaptureDevices),
    mScope: UInt32(kCMIOObjectPropertyScopeGlobal),
    mElement: UInt32(kCMIOObjectPropertyElementMain))
let result = CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &address,
                                     0, nil, UInt32(MemoryLayout<UInt32>.size), &enabled)
guard result == 0 else { status(["status": "capture_error", "message": "CMIO error \(result)"]); exit(1) }
if listOnly {
    RunLoop.current.run(until: Date().addingTimeInterval(2))
    emit(["devices": AVCaptureDevice.devices(for: .muxed).map { $0.localizedName }])
    exit(0)
}
var running = true
signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
let sources = [SIGINT, SIGTERM].map { signalNumber -> DispatchSourceSignal in
    let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
    source.setEventHandler { running = false }
    source.resume()
    return source
}
let context = CIContext()
let end = ProcessInfo.processInfo.systemUptime + duration
emit(["status": "waiting_usb_screen"])
while running && ProcessInfo.processInfo.systemUptime < end {
    let candidates = AVCaptureDevice.devices(for: .muxed).filter { deviceName == nil || $0.localizedName == deviceName }
    guard candidates.count == 1 else {
        status(["status": candidates.isEmpty ? "capture_unavailable" : "multiple_devices",
                "device_count": candidates.count])
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        continue
    }
    let session = AVCaptureSession()
    let receiver = Receiver()
    let output = AVCaptureVideoDataOutput()
    output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
    output.alwaysDiscardsLateVideoFrames = true
    output.setSampleBufferDelegate(receiver, queue: DispatchQueue(label: "gomoku-usb-frames"))
    do {
        let input = try AVCaptureDeviceInput(device: candidates[0])
        session.beginConfiguration()
        guard session.canAddInput(input), session.canAddOutput(output) else {
            session.commitConfiguration()
            throw NSError(domain: "GomokuCapture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot configure USB screen"])
        }
        session.addInput(input)
        session.addOutput(output)
        session.commitConfiguration()
        session.startRunning()
        emit(["status": "capture_started", "device": candidates[0].localizedName])
        var lastSequence = -1
        var lastFrame = ProcessInfo.processInfo.systemUptime
        while running && ProcessInfo.processInfo.systemUptime < end && candidates[0].isConnected {
            RunLoop.current.run(until: Date().addingTimeInterval(interval))
            let (buffer, sequence, date) = receiver.latest()
            if ProcessInfo.processInfo.systemUptime - lastFrame > 5 { break }
            guard let buffer = buffer, sequence != lastSequence else { continue }
            lastSequence = sequence
            lastFrame = ProcessInfo.processInfo.systemUptime
            try autoreleasepool {
                let ci = CIImage(cvPixelBuffer: buffer)
                guard let image = context.createCGImage(ci, from: ci.extent) else { return }
                let bitmap = NSBitmapImageRep(cgImage: image)
                guard let jpeg = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.95]) else { return }
                try jpeg.write(to: dir.appendingPathComponent("latest.jpg"), options: .atomic)
                status(["status": "capturing", "sequence": sequence, "captured_at": date.timeIntervalSince1970,
                        "width": image.width, "height": image.height, "device": candidates[0].localizedName])
            }
        }
        session.stopRunning()
    } catch {
        status(["status": "capture_error", "message": error.localizedDescription])
        emit(["status": "capture_error", "message": error.localizedDescription])
        RunLoop.current.run(until: Date().addingTimeInterval(1))
    }
}
status(["status": "capture_stopped"])
emit(["status": "capture_stopped"])
for source in sources { source.cancel() }
