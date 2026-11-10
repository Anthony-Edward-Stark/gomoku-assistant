import AppKit
import Foundation

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for (name, pixels) in [("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32),
                       ("icon_32x32@2x", 64), ("icon_128x128", 128), ("icon_128x128@2x", 256),
                       ("icon_256x256", 256), ("icon_256x256@2x", 512),
                       ("icon_512x512", 512), ("icon_512x512@2x", 1024)] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                 colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(pixels)/1024, y: CGFloat(pixels)/1024)
    NSColor(calibratedRed: 0.08, green: 0.14, blue: 0.25, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 80, y: 80, width: 864, height: 864), xRadius: 184, yRadius: 184).fill()
    NSColor(calibratedRed: 0.68, green: 0.77, blue: 0.90, alpha: 0.5).setStroke()
    let grid = NSBezierPath()
    grid.lineWidth = 9
    for i in 0..<5 {
        let coordinate = CGFloat(248 + 132*i)
        grid.move(to: NSPoint(x: coordinate, y: 248)); grid.line(to: NSPoint(x: coordinate, y: 776))
        grid.move(to: NSPoint(x: 248, y: coordinate)); grid.line(to: NSPoint(x: 776, y: coordinate))
    }
    grid.stroke()
    for (x, y, white) in [(380, 512, false), (512, 512, true), (644, 644, false)] {
        (white ? NSColor(calibratedWhite: 0.96, alpha: 1) : NSColor(calibratedWhite: 0.035, alpha: 1)).setFill()
        let circle = NSBezierPath(ovalIn: NSRect(x: x-55, y: y-55, width: 110, height: 110))
        circle.fill()
        NSColor(calibratedWhite: white ? 0.78 : 0.3, alpha: 1).setStroke()
        circle.lineWidth = 4; circle.stroke()
    }
    NSGraphicsContext.restoreGraphicsState()
    let data = bitmap.representation(using: .png, properties: [:])!
    try data.write(to: directory.appendingPathComponent(name + ".png"))
}
