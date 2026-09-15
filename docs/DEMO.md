# Demo 本地运行

## 环境

- Python 3.10+
- 阿里云百炼北京地域 API Key 与 Workspace ID

## 启动

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# 复制 .env.example 为 .env，并填入 DASHSCOPE_API_KEY。
# 如果使用工作空间密钥，同时填写 DASHSCOPE_WORKSPACE_ID；可选填写专属 DASHSCOPE_BASE_URL。
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

桌面调试访问 `http://localhost:8000`。手机访问电脑局域网 IP 时，麦克风权限要求 HTTPS，请使用受信任的 HTTPS 开发隧道/反向代理。

如果手机通过局域网 HTTP 打开页面，浏览器会禁用网页直接录音。页面会自动显示音频文件选择入口（`.m4a`、`.mp4`、`.wav`、`.mp3`）；可先用手机“语音备忘录”录音，再在“文件”中选择导出的音频，之后仍会走同一条上传、云端理解、MIDI 语义风格转换和音频渲染流程。使用 HTTPS 时可直接按住主按钮录音。

## 真实录音端到端验收

服务启动后，可用下面的脚本通过真实 HTTP 上传录音并检查 canonical MIDI、模型生成的风格 MIDI 与两个音频路由：

```powershell
.venv\Scripts\python.exe docs\e2e_http_test.py path\to\hum.m4a --base-url http://127.0.0.1:8000
```

脚本不会打印 API Key；它输出任务状态、canonical 音符数量/音高序列、乐句边界、MIDI 往返音符数及 `funk`、`lofi` 路由状态。

## 处理链路

浏览器真录音 → ffmpeg 转单声道 16kHz WAV → Qwen Omni 云端理解整体旋律 → 确定性 YIN 基频与短时能量测量音符音高、起止和停顿 → 保存 canonical `melody_ir.json` 与 `melody.mid` → Qwen Flash（可配置为 Qwen Plus）把 MIDI 作为音乐种子一次性创作完整 Funk/Lofi 多轨 MIDI → 规则合成器渲染 `funk.wav`、`lofi.wav`。

渲染器只读取模型返回的 MIDI 事件，用振荡器和包络做最小声学验证；不会在 MIDI 之外自动补鼓点或伴奏，音色朴素是预期结果。它不调用任何本地模型；云端理解失败时不会返回假结果或回退到本地模型。

### 风格化约束

Funk 和 Lofi 都以同一份 canonical `melody.mid` 为语义输入，由模型一次性创作完整风格 MIDI。模型可以重写音高、时值、时序、和声和配器，不要求逐音复制；服务端只校验结构和数值范围，再渲染成音频。

## 云端设置与限制

- `DASHSCOPE_API_KEY`、`DASHSCOPE_WORKSPACE_ID`、`QWEN_OMNI_MODEL`、`QWEN_TEXT_MODEL` 均为服务端环境变量。风格 MIDI 默认使用响应更稳定的 `qwen-flash`；需要对比时可设置 `QWEN_TEXT_MODEL=qwen-plus`。
- Omni 请求只携带录音，不传歌名、歌词或预期曲谱；Omni 必须确认整体音高走向。逐音 F0、起音和停顿使用确定性声学算法测量，不用 Basic Pitch 等本地模型，也不启用歌曲模板校准。
- Qwen Omni HTTP 接口需要流式响应；模型估计的音高和起止时间可能不准，必须用真实录音验证。
- 当前实现假设模型按 JSON 格式返回 note events；若模型格式变化，任务会报可见错误，不会伪装成功。
- 测试录音会发送至阿里云百炼；仅使用参与者明确同意用于测试的录音。
- 结果临时保存在 `data/demo/`，该目录不提交版本控制。


