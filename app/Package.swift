// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BrickeryApp",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "BrickeryApp",
            path: "Sources/BrickeryApp"
        )
    ]
)
