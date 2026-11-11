import AppKit
import WebKit
import Foundation

func option(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name), index + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[index + 1]
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backend: Process?
    private var output: Pipe?
    private var log: FileHandle?
    private var serviceURL: URL?
    private var session: URL!
    private var closing = false
    private var started = false
    private var stdoutBuffer = Data()
    private let queue = DispatchQueue(label: "gomoku-backend-output")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1160, height: 830),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "五子棋助手"
        window.minSize = NSSize(width: 760, height: 620)
        window.contentView = webView
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        showMessage("正在启动五子棋助手…")
        do { try startBackend() } catch { fail(error.localizedDescription) }
        if let seconds = option("--quit-after").flatMap(Double.init), seconds > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { NSApp.terminate(nil) }
        }
    }

    private func buildMenu() {
        let menu = NSMenu()
        let application = NSMenuItem()
        let applicationMenu = NSMenu()
        applicationMenu.addItem(withTitle: "关于五子棋助手", action: #selector(about), keyEquivalent: "")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "隐藏五子棋助手", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "退出五子棋助手", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        application.submenu = applicationMenu
        menu.addItem(application)
        let edit = NSMenuItem()
        let editMenu = NSMenu(title: "编辑")
        for (title, action, shortcut) in [("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: shortcut)
        }
        edit.submenu = editMenu
        menu.addItem(edit)
        NSApp.mainMenu = menu
    }

    @objc private func about() {
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "五子棋助手", .applicationVersion: "1.2.0",
            .credits: NSAttributedString(string: "腾讯五子棋 USB 屏幕识别与 Rapfi 下一手建议\nRapfi：GPL-3.0，官方权重：CC0\n许可及源代码随 App 资源附带。")
        ])
    }

    private func startBackend() throws {
        guard let resources = Bundle.main.resourceURL else { throw NSError(domain: "Gomoku", code: 1) }
        let directory = resources.appendingPathComponent("backend")
        let executable = directory.appendingPathComponent("gomoku-backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw NSError(domain: "Gomoku", code: 2, userInfo: [NSLocalizedDescriptionKey: "App 的计算程序缺失，请重新解压完整 App。"])
        }
        if let path = option("--session") {
            session = URL(fileURLWithPath: path, isDirectory: true)
        } else {
            session = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                  appropriateFor: nil, create: true)
                .appendingPathComponent("GomokuAssistant", isDirectory: true)
        }
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        let settings = session.appendingPathComponent("settings.json")
        if !FileManager.default.fileExists(atPath: settings.path) {
            try FileManager.default.copyItem(at: resources.appendingPathComponent("default-settings.json"), to: settings)
        }
        let logURL = session.appendingPathComponent("app.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        log = try FileHandle(forWritingTo: logURL)
        try log?.seekToEnd()
        let process = Process()
        process.executableURL = executable
        process.currentDirectoryURL = directory
        process.arguments = ["--port", "0", "--session", session.path, "--duration", "315360000",
                             "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        if let image = option("--image") { process.arguments! += ["--image", image] }
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "PYTHONHOME")
        environment.removeValue(forKey: "PYTHONPATH")
        process.environment = environment
        let pipe = Pipe()
        output = pipe
        process.standardOutput = pipe
        process.standardError = log
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            self?.queue.async { [weak self] in self?.readOutput(data) }
        }
        process.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                guard let self = self, !self.closing else { return }
                if task.terminationStatus == 0 { NSApp.terminate(nil) }
                else { self.fail("计算程序已退出（代码 \(task.terminationStatus)）。日志保存在 \(logURL.path)") }
            }
        }
        backend = process
        try process.run()
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
            guard let self = self, !self.started, !self.closing else { return }
            self.fail("启动超时。日志保存在 \(logURL.path)")
        }
    }

    private func readOutput(_ data: Data) {
        stdoutBuffer.append(data)
        while let newline = stdoutBuffer.firstIndex(of: 10) {
            let line = Data(stdoutBuffer[..<newline])
            stdoutBuffer.removeSubrange(...newline)
            guard let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let address = object["url"] as? String, let url = URL(string: address),
                  url.scheme == "http", url.host == "127.0.0.1" else { continue }
            DispatchQueue.main.async { [weak self] in
                guard let self = self, !self.closing, !self.started else { return }
                self.started = true
                self.serviceURL = url
                self.webView.load(URLRequest(url: url))
                var status = object
                status["app_pid"] = ProcessInfo.processInfo.processIdentifier
                status["session"] = self.session.path
                status["window_visible"] = self.window.isVisible
                self.writeStatus(status)
            }
        }
    }

    private func writeStatus(_ object: [String: Any]) {
        guard let path = option("--status-file"), let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
    }

    private func showMessage(_ message: String) {
        let safe = message.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString("<html><meta charset='utf-8'><body style='background:#0c1423;color:#eef2fa;font:18px -apple-system;padding:40px'>\(safe)</body></html>", baseURL: nil)
    }

    private func fail(_ message: String) {
        guard !closing else { return }
        showMessage(message)
        let alert = NSAlert()
        alert.messageText = "五子棋助手启动失败"
        alert.informativeText = message
        alert.addButton(withTitle: "退出")
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard serviceURL != nil, webView.url?.host == "127.0.0.1", let path = option("--snapshot") else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            let configuration = WKSnapshotConfiguration()
            configuration.afterScreenUpdates = true
            webView.takeSnapshot(with: configuration) { image, error in
                guard let image = image, let tiff = image.tiffRepresentation,
                      let bitmap = NSBitmapImageRep(data: tiff),
                      let png = bitmap.representation(using: .png, properties: [:]) else { return }
                try? png.write(to: URL(fileURLWithPath: path), options: .atomic)
            }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.host == "127.0.0.1" && url.port == serviceURL?.port) {
            decisionHandler(.allow)
        } else {
            if navigationAction.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let process = backend, process.isRunning else { return .terminateNow }
        if closing { return .terminateLater }
        closing = true
        DispatchQueue.global(qos: .userInitiated).async {
            // SIGTERM lets the backend close both its USB capture and Rapfi child.
            process.terminate()
            process.waitUntilExit()
            // AppKit waits in the modal run-loop mode during terminateLater.
            // A callback on the main DispatchQueue cannot reenter a pending quit.
            RunLoop.main.perform(inModes: [.default, .modalPanel]) {
                NSApp.reply(toApplicationShouldTerminate: true)
            }
            CFRunLoopWakeUp(CFRunLoopGetMain())
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        output?.fileHandleForReading.readabilityHandler = nil
        try? log?.close()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
