import Cocoa
import WebKit

// MARK: - 路径
let bundleURL = Bundle.main.bundleURL // .../web-test-agent.app
let appRoot = bundleURL.deletingLastPathComponent() // 产出目录
let resources = Bundle.main.resourceURL ?? bundleURL.appendingPathComponent("Contents/Resources")
let runtimeDir = resources.appendingPathComponent("brickery-runtime")
let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "BrickeryAgent"
let dataDir = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support")
    .appendingPathComponent(appName)
let configPath = dataDir.appendingPathComponent("config.json")

let GUIDE_URL = "http://127.0.0.1:18766/"
let CHAT_URL = "http://127.0.0.1:18767/"

// MARK: - 服务管理
final class ServiceManager {
    private var children: [Process] = []

    func portInUse(_ port: Int) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        p.arguments = ["-iTCP:\(port)", "-sTCP:LISTEN"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run(); p.waitUntilExit() } catch { return false }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return !data.isEmpty
    }

    func launch(_ args: [String], env: [String: String], logName: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3"] + args
        var e = ProcessInfo.processInfo.environment
        for (k, v) in env { e[k] = v }
        p.environment = e
        // 服务日志重定向到 dataDir/<logName>，便于定位启动失败
        let logURL = dataDir.appendingPathComponent(logName)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let fh = FileHandle(forWritingAtPath: logURL.path) {
            p.standardOutput = fh
            p.standardError = fh
        }
        do { try p.run(); children.append(p) }
        catch { NSLog("BrickeryApp: 启动服务失败 \(args.first ?? "") \(error)") }
    }

    func start() {
        NSLog("BrickeryApp: start() 开始")
        try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        let env: [String: String] = [
            "PYTHONPATH": runtimeDir.path,
            "BRICKERY_NO_WATCHDOG": "1",
            "BRICKERY_HOME": dataDir.path,
        ]
        NSLog("BrickeryApp: runtimeDir=\(runtimeDir.path)")
        if !portInUse(18765) {
            NSLog("BrickeryApp: 启动 ipc")
            launch(["-m", "brickery.runtime.ipc", "--home", dataDir.path,
                    "--app-resources", resources.path], env: env, logName: "ipc.log")
        }
        if !portInUse(18766) {
            NSLog("BrickeryApp: 启动 setup_wizard")
            launch(["-m", "brickery.runtime.setup_wizard"], env: env, logName: "setup_wizard.log")
        }
        if !portInUse(18767) {
            NSLog("BrickeryApp: 启动 chat_ui")
            launch(["-m", "brickery.runtime.chat_ui"], env: env, logName: "chat_ui.log")
        }
        NSLog("BrickeryApp: start() 完成，children=\(children.count)")
    }

    func stop() {
        for p in children { p.terminate() }
    }
}

// MARK: - App Delegate
final class AppDelegate: NSObject, NSApplicationDelegate {
    let services = ServiceManager()
    var window: NSWindow!
    var webView: WKWebView!

    func applicationDidFinishLaunching(_ notification: Notification) {
        services.start()
        // 等服务起来再加载页面
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            self?.openPage()
        }
    }

    func openPage() {
        let configured = FileManager.default.fileExists(atPath: configPath.path)
        let url = URL(string: configured ? CHAT_URL : GUIDE_URL)!
        webView.load(URLRequest(url: url))
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false // 关窗隐藏不退出，服务继续
    }

    func applicationWillTerminate(_ notification: Notification) {
        services.stop()
    }
}

// MARK: - 启动
let delegate = AppDelegate()
let app = NSApplication.shared
app.setActivationPolicy(.regular)
app.delegate = delegate

// 菜单栏（退出）
let mainMenu = NSMenu()
let appMenuItem = NSMenuItem()
mainMenu.addItem(appMenuItem)
let appMenu = NSMenu()
appMenu.addItem(NSMenuItem(title: "退出 \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
appMenuItem.submenu = appMenu
app.mainMenu = mainMenu

// 窗口
let window = NSWindow(
    contentRect: NSRect(x: 0, y: 0, width: 920, height: 660),
    styleMask: [.titled, .closable, .miniaturizable, .resizable],
    backing: .buffered, defer: false)
window.title = appName
window.center()
let webView = WKWebView(frame: window.contentView!.bounds)
webView.autoresizingMask = [.width, .height]
window.contentView = webView
delegate.window = window
delegate.webView = webView
window.makeKeyAndOrderFront(nil)
NSApp.activate(ignoringOtherApps: true)

app.run()
