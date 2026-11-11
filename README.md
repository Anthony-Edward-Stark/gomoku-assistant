# 五子棋助手

读取 iPhone 的 USB 或投屏窗口画面，识别 15 路五子棋棋盘，由 [Rapfi](https://github.com/dotb1ue/rapfi) 在本机计算落点。已验证腾讯五子棋的竖屏木质棋盘。适合练习、复盘和允许使用辅助工具的对局，程序不会向手机发送点击。

## 安装与使用

从 [Releases](https://github.com/Anthony-Edward-Stark/gomoku-assistant/releases/latest) 按设备下载：

| 平台 | 安装包 |
| --- | --- |
| Apple 芯片 Mac，macOS 14+ | `GomokuAssistant-macOS-AppleSilicon.zip` |
| Intel Mac，macOS 14+（AVX2/BMI2） | `GomokuAssistant-macOS-Intel.zip` |
| Windows x64 | `GomokuAssistant-Windows-x64.zip` |

**macOS：**解压 ZIP 后打开 App。 USB 连接 iPhone，保持解锁并确认“信任此电脑”，然后显示棋盘。App 为 ad-hoc 签名，未经过 Apple 公证。

**Windows：**解压整个 ZIP，再双击 `GomokuAssistant.exe`。先在电脑上打开 iPhone 投屏窗口（例如已有的 AirPlay 接收器或采集卡预览），然后用 Edge 或 Chrome 打开助手本地页面，点击“选择投屏窗口”，在浏览器中选该窗口。Windows 版不提供 iPhone USB 直连取屏。结束时可点击“停止程序”。Windows 可执行文件未进行商业签名。

计算档位为 250 毫秒至 30 秒，默认 2 秒。支持无禁手、恰好五连及连珠禁手。识别不清或取屏超时会清空建议，可重新定位棋盘或手动定位。

安装包内置 Python、OpenCV 和引擎。取屏与分析在本机运行。Windows 的共享画面只保留在内存。当前识别以清晰、正向棋盘为准，换皮肤或横屏需要重新校准。

## 从源码构建

使用对应架构的 Python 3.12。macOS 需要 Xcode Command Line Tools；Apple 芯片版的 Rapfi 需要 CMake。Windows 构建需要 CMake 和 Visual Studio 2022 或 2026 的 ClangCL x64 工具链。

```sh
python -m pip install -r requirements.txt pyinstaller==6.22.3
python prepare_engine.py
python build_app.py
python verify_packaged.py
python pack_release.py
```

产物位于 `release/`。`prepare_engine.py` 校验并准备该平台引擎；象棋的官方权重仅用作本地验证，不会打入安装包。Mac Intel 源码入口也可直接用 `./run.sh`，默认地址为 `http://127.0.0.1:8765/`。真实手机截图不会随仓库发布。

## 许可

助手自有代码按 [GNU GPL v3.0](LICENSE) 发布。Rapfi 按 GPL-3.0 分发，完整对应源码和上游许可在 [`engine/`](engine/) 中；Rapfi 的独立网络权重按 CC0 分发。引擎及权重的提交、构建参数和 SHA-256 见 [`engine/manifest.json`](engine/manifest.json)。App 另附 Python、NumPy、OpenCV、PyInstaller 等依赖的许可。
