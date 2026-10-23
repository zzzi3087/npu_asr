# 实时字幕 · Intel NPU ASR

Windows 桌面实时转录与翻译软件。支持麦克风和桌面音频、双语字幕、透明桌面字幕、历史记录和导出。

已在 Intel 258v 笔记本成功运行

## 安装与使用

1. 在 [Releases](https://github.com/zzzi3087/npu_asr/releases/latest) 下载 `LiveCaptions-0.2.0-win64-setup.exe` 并安装。更新前先在托盘菜单选择“退出”。也可下载 portable ZIP，完整解压后运行 `LiveCaptions.exe`。
2. 需要 Windows 11 x64、兼容 OpenVINO 的 **Intel NPU** 和已安装的 Intel NPU 驱动。软件自带运行库，不需要另装 Python。不同型号、驱动的编译兼容性可能不同；没有 NPU 时不会改用 CPU/GPU 转录。
3. 首次启动检查模型；缺少的文件自动下载，并显示进度。可取消并在下次续传。下载后校验 SHA-256，完整模型会复用；首次 NPU 编译也需要等待。
4. 在设置中选择模型、源语言、翻译语言和收音设备。顶部的音频来源可快速切换麦克风/桌面音频，点击“开始收音”开始。
5. 翻译需要在设置的云服务页面填写自己的 API 地址、模型和密钥。兼容 OpenAI Chat Completions 和 Anthropic Messages 格式。仅转录时可关闭翻译，无需 API Key。
6. 默认关闭或最小化主窗口后转入右下角系统托盘，任务栏窗口隐藏，收音、翻译和桌面字幕继续运行。点击托盘图标恢复窗口；右键可停止收音、切换桌面字幕、进入设置或完全退出。图标可能在 Windows 的隐藏图标菜单中。
7. 在“设置 → 后台与托盘”可分别关闭上述行为。再次双击程序会恢复已有窗口，不会重复启动转录进程。
8. 转录本地文件时，点击左侧“音频文件 · 队列”，添加或拖入文件；可一次添加多个，查看进度、调整顺序、暂停队列、取消和重试。双击任务或点“查看字幕”可进入历史记录并导出。

安装程序当前没有代码签名证书。仅从本仓库 Releases 获取程序，并可用同页的 `SHA256SUMS.txt` 核对文件。

## 模型与下载

**安装 EXE、便携 ZIP 和源码均不包含模型权重。** 模型按需存到当前 Windows 用户的本地应用数据目录 `ParakeetNPUSubtitles/models`，不会放进安装目录。

| 模型 | 用途 | 下载来源 |
|---|---|---|
| Parakeet TDT 0.6B v2 | 英语，低延迟 | 固定版本的 FluidInference Hugging Face 仓库；分词器来自独立模型资源包 |
| Qwen3-ASR 1.7B | 多语言 | 本仓库独立的 `models-v1` Release，OpenVINO 社区转换版本 |
| Mega-ASR 1.7B | 多语言、抗噪对照 | 同上，合并抗噪分支，未启用动态路由 |
| Silero VAD | 语音活动检测 | 独立模型资源包，仅约 2 MB |

Parakeet 首次下载约 1.2 GB，Qwen/Mega 各约 3 GB；相同文件尽量复用。NPU 编译缓存另占磁盘空间。下载地址、原始来源、大小和 SHA-256 均列在 [model-catalog.json](resources/model-catalog.json)。Qwen 基座采用 Mega-ASR 作者发布的固定 Qwen3-ASR-1.7B 检查点副本，具体提交见清单。

三种 **ASR 神经网络** 都显式在 NPU 执行并检查执行设备，没有 CPU/GPU 转录回退。音频采集、分句、查表和文本解码使用 CPU；轻量 VAD 也在 CPU 执行。Parakeet 的确定性音频前处理可选 GPU，默认 CPU；这不改变 ASR 网络的 NPU 执行。

## 功能

- 本地音频批量转录，队列与每文件历史保存，支持 WAV、MP3、M4A/AAC、FLAC、OGG/Opus 等，也可读取视频的首条音轨。
- 系统托盘后台运行；隐藏时暂缓主页字幕排版，无动画或常驻轮询定时器。
- 设置窗口右上角显示当前程序版本，切换设置页面时始终可见，可选择复制。
- 多语言转录和翻译；Parakeet v2 仅支持英语，多语言使用 Qwen/Mega。
- 动态音量增益、静音门限与分句策略；低音量提升不能恢复录音中已经缺失的信息。
- 领域词表：内置物理学、高能/理论物理、天体物理及暗物质术语，可编辑其他领域。Parakeet 使用 TDT 解码词组偏置；Qwen/Mega 使用上下文词表。增强效果取决于录音，词表过强也可能增加误报。
- 主界面双语字幕、历史搜索、重命名、删除、重试翻译、导出。
- 字幕上方的“不显示原文”“不显示译文”同时控制主页历史与桌面字幕，并记住选择；缺少译文时自动保留原文。两项都勾选则隐藏全部字幕文字。显示开关不删除历史，也不改变完整字幕的导出内容。
- 翻译服务失败时，字幕区只显示可用的文字，错误集中在右下角“翻译 API 不可用”提示中；悬停可看原因。成功重试后提示自动消失。
- 透明置顶桌面字幕：默认白字黑边、最近两句及翻译，可调整字体、颜色、字号、间距并预览。
- 自定义全局配色、保存主题、配置导入/导出。
- 停止收音时不持续进行 ASR 推理；下载进度和界面刷新节流，无持续动画。

## 本地文件与预处理

Windows 安装包和便携版自带 FFmpeg/ffprobe，无需额外安装。每个文件显示“检查响度 → 预处理 → NPU 转录”的阶段进度与总体进度；模型首次下载/加载也会在主页显示。

- 转录前测量时长、采样率、声道、LUFS、真峰值、RMS 和直流偏置。
- 默认按需做恒定音量缩放：目标约 −20 LUFS，偏差不足 2 dB 时通常保持原音量；提升最多 24 dB，且受 −2 dBTP 峰值余量限制。受峰值或增益限制时可能达不到目标，识别阶段仍可使用已有的动态增益设置。
- 静音不放大；已削波的文件显示提示。混合声道出现明显抵消时自动选取较强声道，避免反相立体声把语音抵消。
- 统一为 16 kHz 单声道浮点 PCM；兼容且无需调整的 WAV 直接复用。可选 80 Hz 低频过滤，默认关闭；不默认做强降噪、不剪静音、不改变播放速度或时间轴，原文件不会被改写。
- 分块读取，临时缓存每小时约 230 MB，任务结束/取消后清理。队列暂停会等待当前文件结束；“取消当前”会终止 FFmpeg，或等待正在执行的 NPU 推理结束，已生成字幕保留。
- 文件与实时收音串行使用同一个 NPU。收音时添加的文件先排队，停止收音后开始；也可暂停文件队列再收音。翻译在后台继续，显示“转录完成”不表示网络翻译已经全部结束。
- 队列只在本机保存文件路径和任务配置，重启后等待用户继续；中断项可重试，会生成新的历史记录，不覆盖上次已保存的字幕。

所有 ASR 网络仍仅在 NPU 执行。FFmpeg 解码、响度分析、采样转换和轻量 VAD 使用 CPU；FFmpeg 使用低优先级和受限线程，不引入常驻后台进程。

## 隐私与本地数据

- 麦克风/桌面音频在本机转录，程序不向云端发送音频，不默认保存录音。
- 开启翻译后，字幕文字及有限上下文会发送至你设置的服务地址。使用前请确认该服务符合你的需求。
- API Key 存放在 Windows 凭据管理器，不写入普通设置文件或配置导出。历史字幕与设置存在本机用户目录；导出历史时会包含所选字幕。
- 下载只访问清单所列的公开模型地址，不附带翻译服务密钥。
- 本地文件的音频内容和队列路径不上传；若启用翻译，转录后的文字仍会发给你配置的服务。
- 发布源码采用独立的干净历史，仅含程序、构建脚本和公共资源。个人配置、字幕历史、录音、缓存、日志、旧开发报告均不发布。

## 从源码运行和构建

使用 64 位 Python 3.13，创建虚拟环境后：

```powershell
python -m pip install -r requirements/runtime.txt
python main.py
```

源码运行本地文件功能还需要 `ffmpeg`、`ffprobe` 在 PATH 中，也可将 `PARAKEET_FFMPEG_DIR` 指向包含这两个程序的目录。

构建 Windows 包需要同一运行环境、`packaging/requirements-build.txt` 和 Inno Setup 7：

```powershell
python -m pip install -r packaging/requirements-build.txt
python tools/prepare_build.py
python tools/prepare_media_tools.py
python -m PyInstaller --noconfirm packaging/LiveCaptions.spec
ISCC /DBundleDir="<absolute-path-to-dist/LiveCaptions>" /DReleaseDir="<absolute-output-folder>" packaging/installer.iss
```

模型转换开发另见 `tools/export_qwen_npu.py`、`tools/export_mega_npu.py` 和 `requirements/conversion.txt`，普通用户无需转换。`PARAKEET_DATA_DIR` 和 `PARAKEET_MODEL_ROOT` 可覆盖本地数据/模型目录，主要用于隔离测试。

## 源码目录

```text
main.py        源码启动入口
src/           程序代码（界面、音频、模型与翻译）
tests/         隔离测试与回归检查
tools/         模型转换和构建辅助工具
packaging/     Windows 打包配置
resources/     模型下载清单、图标与公开元数据
requirements/  运行和转换依赖
licenses/      第三方许可与版权说明
```

离线检查：

```powershell
python tests/check_model_downloads.py
python tests/check_file_processing.py
python tests/check_file_queue.py
python tests/check_caption_visibility.py
python tests/check_tray.py
python tests/check_main_layout.py
python tests/check_model_startup.py
python tests/check_audio_ui.py
python tests/check_ui_stability.py
```

安装后的可执行文件另提供 `--smoke-test --report <local-json-path>`，用于隔离测试界面、音频运行库和 NPU。加 `--model parakeet --wav <wav-path>` 可验证真实模型转录；缺少模型会下载。测试不会启动麦克风收音。

## 更新预告:
下个版本将会加入:
多语言支持
省电优化
界面优化
本地文件转录单独选择模型



## 许可与致谢

程序源码采用 Apache-2.0；改编的 MIT 代码保留原许可。感谢 FluidInference/eddy、Nerdstorm/LiveTranscribe、NVIDIA、Qwen、Mega-ASR 作者、OpenVINO、Silero 和 Qt 等项目。程序与独立下载的模型分别遵循各自许可，详见 [THIRD_PARTY_NOTICES.md](licenses/THIRD_PARTY_NOTICES.md) 和 `licenses/third-party/`。
