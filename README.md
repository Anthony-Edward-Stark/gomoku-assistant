# 五子棋助手

在 Mac 上读取 USB 连接的 iPhone 画面，识别 15 路五子棋棋盘，由 [Rapfi](https://github.com/dotb1ue/rapfi) 在本机计算落点。已验证腾讯五子棋的竖屏木质棋盘。适合练习、复盘和允许使用辅助工具的对局，程序不会向手机发送点击。

## 安装与使用

1. 从 [Releases](https://github.com/Anthony-Edward-Stark/gomoku-assistant/releases/latest) 下载 `GomokuAssistant-macOS-Intel.zip`，解压后打开“五子棋助手.app”。支持有 AVX2/BMI2 的 Intel Mac，最低 macOS 14。
2. 用 USB 连接 iPhone，保持解锁。首次连接时在手机上确认“信任此电脑”，再打开棋盘。
3. 选择执子颜色、规则和计算时间。蓝圈标出建议落点；计算期间建议会持续更新。

1.1 版支持 250 毫秒至 30 秒共八档，默认 2 秒。棋盘变化时停止旧计算。支持无禁手、恰好五连及连珠禁手。识别不清或取屏超时会清空建议，可以用“重新定位棋盘”或手动定位恢复。其他棋盘皮肤与横屏未验证。

安装包内置 Python、OpenCV、取屏程序和 Rapfi。App 为 ad-hoc 签名，未经过 Apple 公证。设置和取屏数据保存在本机 `~/Library/Application Support/GomokuAssistant/`，不会发送到网络。

## 从源码运行或构建

在符合上述条件的 Intel Mac 上安装 Python 3.12 和 Xcode Command Line Tools。`./run.sh` 会创建本地虚拟环境、安装 `requirements.txt` 中的依赖并编译取屏程序。默认窗口地址为 `http://127.0.0.1:8765/`，运行数据保存在仓库的 `.runtime/`。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller==6.22.3
.venv/bin/python verify.py
.venv/bin/python build_app.py
```

构建产物为 `dist/五子棋助手.app`。离线回放可用 `./run.sh --image /path/to/board.jpg`。回归脚本默认运行引擎和状态检查；完整的截图识别与搜索基准需要有标注的原始样本，个人采集画面未随仓库发布。

## 许可

助手自有代码按 [GNU GPL v3.0](LICENSE) 发布。Rapfi 按 GPL-3.0 分发，完整对应源码和上游许可在 [`engine/`](engine/) 中；Rapfi 的独立网络权重按 CC0 分发。引擎及权重的提交、构建参数和 SHA-256 见 [`engine/manifest.json`](engine/manifest.json)。App 另附 Python、NumPy、OpenCV、PyInstaller 等依赖的许可。
