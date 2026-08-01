# HatTip Lab — Windows 智能体皮套桌宠

HatTip Lab 是一个透明、无边框的 Windows 桌宠前端。角色表现和智能体后端彼此独立：用户可以接入本机 Hermes CLI、OpenAI/GPT，或任意兼容 OpenAI Chat Completions 的 HTTP 服务，也可以自行导入拥有使用权的 Live2D 模型。

## 当前能力

- GIF / Live2D 桌宠窗口，可长按左键拖动、缩放、置顶和最小化；
- 单击角色打开文字输入，点击桌面或其他窗口关闭；
- 回复气泡根据角色位置自动显示在左侧或右侧；
- 三个统一的流式 Provider：Hermes CLI、OpenAI Responses API、通用 HTTP；
- 独立设置窗口：Provider、模型参数、Live2D、语音和快捷键；
- API Key 使用当前 Windows 用户的 DPAPI 加密后保存；
- 全局按住说话，默认 `Alt+Space`，松开后识别并发送；
- 开箱即用的 Edge 在线朗读、Piper 离线朗读、可选 GPT‑SoVITS 情感音色，以及自动降级和基础 Live2D 口型动画；
- 系统托盘、最小化到托盘，以及 125% / 150% DPI 适配；
- 用户自行导入 Live2D，不内置 Haru、第三方模型或 Cubism Core；
- PyInstaller 便携包和 GitHub Actions 发布流程。

## 普通用户使用

从 GitHub Releases 下载 `HatTipLab-windows-x64.zip`，解压后运行 `HatTipLab.exe`。首次启动默认连接本机 Hermes；在角色上单击右键，选择“设置…”可以切换后端。

从旧版升级时，程序会自动继续读取已有的设置、Live2D 模型与语音资源，无需重新导入。

### Hermes CLI

确认终端中的以下命令可以正常返回：

```powershell
hermes chat -q "你好"
```

HatTip Lab 会直接使用同一 Windows 用户环境中的 `hermes` 命令。

### OpenAI / GPT

在“设置…”中选择“OpenAI / GPT”，填写自己的 API Key 和模型 ID。默认使用 Responses API、`store: false`，密钥不会进入日志，也不会以明文写入设置文件。

也可以在启动程序前设置环境变量：

```powershell
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_MODEL = "你可用的模型 ID"
```

### 任意兼容服务

在“设置…”中选择“HTTP / OpenAI 兼容”，填写服务根地址、模型 ID 和可选 API Key。例如地址为 `http://127.0.0.1:11434` 时，程序会请求：

```text
http://127.0.0.1:11434/v1/chat/completions
```

也可以直接填写以 `/v1` 或 `/chat/completions` 结尾的完整兼容地址。

## 语音

在设置中开启语音输入后：

1. 按住 `Alt+Space`，角色显示“正在聆听”；
2. 说完后松开，识别文字会直接发送给当前 Provider；
3. 也可以单击角色，再按住输入框旁的圆点麦克风；
4. 开启 TTS 后，可选择 Edge、Piper 或 GPT‑SoVITS 作为首选；首选不可用时会自动降级，最后始终保留文字回复。

Edge 是默认推荐项，无需下载语音模型，但朗读时需要联网。Piper 合成过程完全离线；由于当前中文社区模型的再分发许可仍有不明确之处，公开包暂不偷带 `huayan` 等权利不清的模型，请在“语音”页导入你有权使用的 `.onnx` 与同名 `.onnx.json`。GPT‑SoVITS 可从同一页面一键安装到独立用户目录，首次安装通常需要 5–10 分钟并占用数 GB；参考音频只保存在 `%APPDATA%/HatTipLab/voices/`。详细许可边界见 [docs/TTS_MODELS.md](docs/TTS_MODELS.md)。

语音识别可能使用 Windows WebView2 的在线能力。首次使用时，请允许 Windows 桌面应用访问麦克风。

## 导入 Live2D

公开版本不会分发 Haru 或其他许可边界不明确的示例模型。使用方法：

1. 右键角色 →“设置…”；
2. 在“角色”区域安装你从合法来源取得的 `live2dcubismcore.min.js`；
3. 导入自己的 `.model3.json`、`.model.json` 或 ZIP 模型包；
4. 选择模型并切换为 Live2D。

模型和 Cubism Core 只保存在当前用户数据目录，不会进入源码或发布包。获取、制作、授权和模型包格式说明见 [docs/LIVE2D_MODELS.md](docs/LIVE2D_MODELS.md)。

## 开发运行

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run-gif.ps1 --mock-hermes
```

连接真实 Hermes：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-gif.ps1
```

诊断和测试：

```powershell
.\.venv\Scripts\python.exe .\desktop_pet.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\tools\release_audit.py
```

独立桥接服务：

```powershell
.\.venv\Scripts\python.exe .\hermes_vpet_bridge.py 8766
```

- `GET /health`：运行状态和 Provider 列表；
- `GET /providers`：智能体能力；
- `POST /chat`：默认返回 SSE，例如 `{"provider":"hermes","text":"你好"}`；
- `POST /hyper_stream/success`：兼容旧版 SSE 地址。

## 构建 GitHub 发布包

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

输出为 `HatTipLab-windows-x64.zip`。仓库中的 GitHub Actions 会在推送 `v*` 标签或手动触发时运行测试、素材审计和打包。

## 代码结构

```text
web/                       桌宠、设置页和 GIF / Live2D 前端
desktop_pet.py             Windows 宿主、窗口和本地 API
agent_providers.py         Provider ABC、Hermes/OpenAI/HTTP 与注册表
hermes_vpet_bridge.py      SSE/JSON、UI、模型和 TTS 本地服务
voice_services.py          全局按住说话、Piper / GPT‑SoVITS 与降级链
model_library.py           Live2D 导入、验证和用户模型库
settings_store.py          设置持久化与 Windows DPAPI 密钥保护
model-pack-template/       不含角色素材的模型包声明模板
tools/release_audit.py     发布素材边界审计
```

第三方 Provider 需要实现 `AgentProvider.info()` 和异步流式 `chat(text, history)`，再注册到 `ProviderRegistry`。模型包始终是纯数据包，不能携带可执行插件。

## 发布原则

- 默认 GIF 和项目代码可以随 GitHub Release 发布；
- 不发布用户模型、Live2D 示例原始文件或 Cubism Core；
- 不发布用户参考音频、GPT‑SoVITS 安装目录或未经逐项审核的 Piper 模型；
- API Key 不进源码、发布包或日志；
- 当前 Provider 均为聊天接口。执行外部任务所需的权限中心和插件 SDK 属于后续阶段。

开源前端依赖及许可见 [web/vendor/THIRD_PARTY_NOTICES.md](web/vendor/THIRD_PARTY_NOTICES.md)。
