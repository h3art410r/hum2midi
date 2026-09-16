# 哼唱转 MIDI：模型复核与替换建议

日期：2026-09-15。目标：快速判断错误来自接入代码还是云模型，选出值得实际替测的模型。

## 复核结论

主要瓶颈是当前 Qwen Omni 的逐音转谱能力，不能将其泛化成“模型完全不能识别音高”。本轮未向模型提供歌名、歌词或任何参考音符。

使用生产提示词与相同兼容 API，新增两次 qwen3.5-omni-plus 请求：

| 输入 | 结果 | 判断 |
| --- | --- | --- |
| 6 秒、5 个纯音，真实音高 57/64/60/67/59，不等时值与停顿 | 5 个音高全部正确；起音平均绝对误差约 0.20 秒，时值平均绝对误差约 0.432 秒，最后音符甚至超过片段结尾 | 音频确实进入模型，简单音高可识别，但时间估计不可靠 |
| 用户 10.516 秒录音，16kHz 单声道 PCM16 WAV | 返回 14 音，音高 55/55/55/57/55/53/52 重复两遍；多数音符等间隔 | 与用户说明的旋律轮廓不符，真实哼唱未通过 |

两次 HTTP 200、finish_reason=stop；响应 model 与请求一致，usage 包含音频 token。保留了原始文本、usage、请求 ID、WAV 参数和 SHA256。模型原始音高与 MIDI 往返解析的音高一致，排除了音高在 JSON→MIDI 环节被改写的情况。这不代表渲染器已全面验证。

可复现脚本：`docs/diagnose_audio_model.py`，项目根目录执行 `.venv\Scripts\python.exe docs/diagnose_audio_model.py`。原始证据存放在忽略目录 `data/demo/model-audit/`；合成控制音频仅用于诊断，不进入 Demo，也不作为真人识别结果。

早期部分离线测试曾将 M4A 标记为 WAV，其结果不能作为有效对比。本轮使用有效 WAV。已在生产适配器加 WAV 格式校验，防止再次发生。先前已对其他 Omni 版本做过 WAV 测试，均未得到可用旋律；本轮为节省时间和费用没有重复全部测试。

## Qwen Omni 基础听音探测（2026-09-15）

- 已按阿里云当前文档重新核对 API 音频链路：OpenAI 兼容接口使用 `input_audio`，`data` 可传 Base64 Data URL，需指定 `format` 并启用流式调用；当前客户端的 WAV Data URL、`format=wav` 和 `stream=true` 均符合文档。百炼回包含 `audio_tokens=72`，证明音频模态确实进入请求；官方计费说明按音频时长折算音频 token。该证据确认调用链正确，但不单独证明逐音转谱准确。参见 [Qwen-Omni 音频输入](https://help.aliyun.com/zh/model-studio/qwen-omni) 与 [OpenAI 兼容接口字段](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。
- 使用 `qwen3.5-omni-plus`，输入同一段原始哼唱转码 WAV（16kHz、单声道、PCM16、10.516 秒）；只要求自然语言描述声音类型、旋律高低变化、节奏和停顿，不要求生成 MIDI，也不提供曲名、歌词或曲谱。
- HTTP 200、正常结束；usage 记录 72 个 audio tokens。模型判断为一个人用“嗯”声哼唱，并提到旋律有高低起伏、节奏偏自由、乐句间有停顿。描述较概括，没有给出可核验的具体音符或精确乐句数。
- 后续追问音符数、乐句数、停顿和拍感/BPM；模型估计约 15 个音、2 个乐句（8+7），第 4 秒附近停顿，速度 60–70 BPM，并称节拍倾向不明显。模型说明数字不确定。项目负责人已人工确认本录音确为两句、共 14 个音符；逐音高与 BPM 仍需要继续对照试听。
- 再追问按时间顺序描述音高升降、重复音和停顿，不要 MIDI/音名/乐谱；模型给出两段按秒的细节，但无法数出重复音具体次数，且补充了多项录音本身难以核实的解释。响应完整结束（755 completion tokens），记录在 `data/demo/qwen-humming-probe/contour-detail.json`。与上一问的“约 15 音”相比，这次承认无法可靠计数，说明结构描述不等于逐音辨识；时间和趋势仍需对照独立标注，不能据此认定能力已验证。
- 再要求仅凭录音给相对简谱，说明主音参照、重复音、延音与停顿；模型回答无法判断绝对音高，并输出全为 `1` 的序列（四小节重复），没有表达先前所称的旋律起伏，也未遵守“听不清写问号”。这次虽顺利返回，但内容不足以验证它识别出旋律，反而暴露出格式服从/旋律辨识问题。原始记录：`data/demo/qwen-humming-probe/jianpu.json`。
- 针对“音高高低”做定向测试：用 `qwen3.5-omni-flash` 对完整 10.516 秒录音只做四选一的整体走向判断（先升后降/先降后升/基本不变/无法判断），两次均答“先逐渐升高后逐渐降低”。这与独立的 Basic Pitch 基准序列相符：开头约 MIDI 44–45，随后升到 MIDI 52–54，再逐步降到 MIDI 44–46。提示词、模型回复与用量在 `data/demo/qwen-humming-probe/pitch-contour-choice-flash.json`；可复现：`.venv\\Scripts\\python.exe docs/probe_hum_audio_understanding.py --model qwen3.5-omni-flash --pitch-contour-choice --out data/demo/qwen-humming-probe/pitch-contour-choice-flash.json`。
- 单音辅助验证中，Flash 对两个独立片段重复播放后的基频估计分别为：较低片段约 100–125 Hz、较高片段约 150–250 Hz，排序正确；另一组点估计为 110 Hz 与 130 Hz，仍排序正确，但较高片段低估明显。Plus 的同类估计与高低比较多次不稳定；直接 A/B 问法还出现“总选第一段”的顺序偏差（把高低片段倒序后仍选第一段）。这些原始请求分别保存在 `pitch-single-low-flash.json`、`pitch-single-high-repeated-flash.json`、`pitch-single-low2-point.json`、`pitch-single-high2-point.json` 及各 `pitch-ab-*.json` 中。实验裁剪/重复仅用于探针，不作为产品输入流程。
- 判断：**已确认 Flash 能从这段真人哼唱里抓到整体的音高高低走向**，达到当前“辨认高低”的粗粒度目标；但它的逐片段基频估计仍较粗，A/B 相对判断存在顺序偏差，不能据此声称逐音音高/时值已可靠识别或已解决 MIDI 转谱问题。后续若继续转谱，应另做带人工真值的逐音评测。
- 结论：能做粗粒度音频分类和音乐描述；这不证明它能准确识别音高、重复起音和时值，不能据此改变之前对 MIDI 转谱精度的判断。
- 可复现脚本：`docs/probe_hum_audio_understanding.py`；基础描述与结构追问分别保存在忽略目录 `data/demo/qwen-humming-probe/response.json` 和 `count-structure.json`。

## 真实 MIDI 生成链路复核（2026-09-15）

- 用项目原转谱提示词在 `qwen3.5-omni-flash` 上重新跑完整真实录音，服务端 JSON/MIDI 解析成功，但模型输出 25 个音符，全部按 0.35 秒等间隔排列，音符序列为 D/E/F/G 一类升降阶梯并循环；与波形中明显的停顿、音高区间和短时能量起音不相符。confidence 均报 0.9，不能当作准确率。
- 同一次生产诊断的已知纯音控制（真值音高 57/64/60/67/59）只输出四个音，且错为 69/71/73/75；这表明 Flash 的结构化逐音输出连简单校准样例也未通过。把真实录音切成两句后再要求简短 JSON，第一句只输出两个超长音，第二句输出七个全为 MIDI 60 的等间隔音；分句和缩短提示词未解决逐音识别。
- 因此主流程改为“云模型必需的整体音高走向校验 + 非 ML 的确定性声学测量”。Qwen Flash 对原始整段录音连续两次选择“先升后降”；同音频的确定性 YIN + 短时能量边界提取出 14 个发声事件、两句（第二句起点约 5.64 秒），音高为 `44,44,52,52,54,54,52,50,49,48,47,46,45,44`，速度估计约 102.56 BPM。两者的整体走向一致；算法没有本地模型权重和歌曲模板。若云端请求失败或方向不一致，任务失败而不转成本地模型。
- 真实录音通过 FastAPI 上传链路端到端生成了 `melody_ir.json`、`melody.mid`、旋律参考 `original.wav`、`funk.wav` 与 `lofi.wav`；IR MIDI 往返解析为 14 个音符，音高与起点逐项保留，保存 102.56 BPM 和乐句边界。三个 WAV 内容不同。最新版结果留在本机忽略目录 `data/demo/8d307318c4d948e5b97f81976775632e/`；机器校验通过仍不等于用户主观试听验收。
- 重新取用户原始 M4A（175,287 bytes）以 `audio/mp4` 走真实上传接口，服务端 FFmpeg 转码 WAV 与已分析 PCM 的 SHA-256 完全一致；任务完成并生成全部 4 个非空产物。再将同一段录音编码成 48kHz/64kbps Opus WebM，以 `audio/webm;codecs=opus` 上传：任务同样完成，14 音音高序列、102.56 BPM 和 5.64 秒句界均一致；两条上传路径生成的 MIDI 和三份渲染 WAV SHA-256 完全一致。M4A 结果在 `data/demo/c4b62d3ce4b44c5ab9cc2477baa3cfbb/`，WebM 结果在 `data/demo/d4598c6055c24026aa5803edb4b1d6bb/`。
- 又启动本机 Uvicorn HTTP 服务，在不重启服务的同一会话里顺序提交真实 M4A、Opus WebM 和 WAV 三次；三项均返回 completed、14 个音符、102.56 BPM、同一 MIDI SHA-256（`98301b45e4323579`），没有 API 或渲染错误。记录的最新任务目录依次为 `data/demo/a664bb80858747bba024ce0e251b588f/`、`data/demo/d3d53a319f0f4dc99904b47dbca96c5f/`、`data/demo/c32a2660a5d2433a810db5b9e9d494c8/`。
- 通过真实 Uvicorn HTTP 再提交 M4A 后，从同一 job 分别 GET `original`、`funk`、`lofi` 音频路由，全部返回 200、`audio/wav` 和非空内容。验证任务目录为 `data/demo/7d8731d13fec4c98b721103e75eb9968/`。
- 将原录音与合成参考顺序拼接后盲问 Flash，它判断音高顺序、重复音数量和起音间隔匹配，但认为合成版没有乐句停顿。对两段 WAV 做短时能量复核发现原始录音停顿约 5.05–5.64 秒，合成参考 MIDI 同样在约 5.06–5.64 秒静音；此处是模型误判，说明模型的音频比较不能当作结构真值。完整问答记录在 `data/demo/qwen-humming-probe/original-vs-midi.json`。重采样后的合成参考再经声学提取，得到同样的 14 音序列，起点最大偏差约 10ms。
- 本轮复核发现音符区间两端的滑音会把一个边界音推高一个半音。现在音高只在每个发声区间中央 50% 测量（两侧最多各裁 150ms），将约 7.40 秒的事件由 MIDI 48 改为 47。以时间重叠加权的主音高逐事件对齐 Basic Pitch 后，14 个事件中 13 个一致；唯一分歧在首音附近，YIN 稳态中位数约 104.4Hz（MIDI 44），Basic Pitch 该区域的时序片段在 44/45 间跳动且以 45 稍多。Basic Pitch 只用于离线对照，没有进入产品运行链路。更新后，降低真实录音增益并叠加确定性白噪声（0.015 RMS），仍得到相同 14 音、高低走向和约 5.65 秒乐句边界。
- 再用完全独立的 librosa 0.11.0 YIN 与 pYIN 在同一批 14 个稳定音符区间复算基频；两种传统非 ML 算法均有 14/14 个四舍五入后的 MIDI 音高与 IR 一致。可复现脚本 `docs/validate_pitch_trackers.py`，在已有离线 librosa 环境执行 `data/demo/basic-pitch-env/Scripts/python.exe docs/validate_pitch_trackers.py`；结果、音频 SHA256 和每音中位音高写入忽略目录 `data/demo/pitch-trim-validation/independent-pitch-check.json`。这确认提取音高是稳定声学基频，不等于用户主观旋律验收。
- 负责人此前说明原哼唱是《小星星》。仅作为事后人工基准（没有把曲名/曲谱送给模型，也未将模板用于程序），按提取的第一个音 MIDI 44 移调标准 14 音旋律后，当前声学序列每个音都在目标音上下 1 个半音内，14 个中 6 个精确相同；偏差方向随录音片段变化，说明不应直接用标准曲谱覆盖真实哼唱的音高。此基准支持整体旋律近似匹配，但不能取代负责人对 MIDI 参考的试听。
- 对 Omni 的进一步核验没有证明逐音识别可靠：Flash 被要求只依录音列出半音变化和起音时间，回答 15 音、变化序列含多个与声学轨迹不符的跳变，且起音时间延伸到 12.93 秒（录音实际长 10.516 秒）。提示词与完整响应保存在 `data/demo/qwen-humming-probe/interval-sequence-flash-utf8.json`。这说明 Omni 能识别整体高低走向和粗略单音高低，但逐音轨迹仍须逐音对照，不能把格式完整或自报置信度当准确率。
- 对当前 22.305 秒原唱+1秒间隔+MIDI参考 A/B 文件再次询问 Flash。它正确数出原唱 14 音，却声称 MIDI 有 15 音、开头音高顺序不同；当前 IR/MIDI 解析实际均为 14 音且与源事件逐项一致。该对照回复存在可直接由文件解析证伪的错误，不作为试听真值；原始问答在 `data/demo/qwen-humming-probe/ab-current-flash.json`。
- 确定性提取实现位于 `app/pitch_tracking.py`；音符两侧滑音不参与音高取整，连续发声时以稳定的基频阶跃辅助分音。`tests/test_pitch_tracking.py` 现有 7 项检查覆盖纯音校准音高/起止/时长、真实录音音符与句界、反向高低走向、低增益加噪后的识别稳定性、158 BPM 高音域快速重复、无静音的 166.67 BPM 连音序列（8 个音符全部正确，起音误差小于 20ms），以及真实录音从声学提取到 MIDI 再解析的音高/起音/时值保持。测试命令：`.venv\\Scripts\\python.exe -m unittest discover -s tests -v`。
- 用户确认的真实录音再次通过 Qwen Flash 整体走向校验并由当前处理链重建 MIDI：14 个事件、两句（5.64 秒起第二句）、102.56 BPM；对应 MIDI 及原始旋律/Funk/Lofi 音频重渲染于 `data/demo/pitch-trim-validation/`。该记录的逐音序列为 `44,44,52,52,54,54,52,50,49,48,47,46,45,44`。13 个相邻起音间隔中，12 个普通间隔落在 0.56–0.62 秒，句间间隔约 1.14 秒；所以以“一个音符起音为一拍”计算约 102.56 BPM，IR 仍直接保留逐音起点及时值。原唱后接 MIDI 渲染的 A/B 文件为 `original-vs-midi.wav`。精细音高和节奏仍需负责人试听验收。
- 最后又将原始 M4A 提交到真实 FastAPI HTTP 上传路由（job `7120983acb5a42b49e3de811ed877b16`）；云端校验通过、状态 completed、14 个音符，`original`/`funk`/`lofi` 下载路由均 HTTP 200。MIDI 与旋律参考 WAV 哈希分别与独立重建件相同，证明当前 HTTP 端到端输出确定且一致。
- 为了让现场调试能逐音审查，在结果 API 暴露精简的 tempo/phrase/note 事件，并在桌面 16:9 页面加入可展开的时间×音高图与每音列表；没有把完整内部 IR 或置信度发给前端。`tests/test_generation_api.py` 验证数据形状，Node 渲染检查验证真实 14 音样例出现 14 个音符条块、14 个列表项和一条乐句线。
- 真人音频样本盘点：`data/demo/` 与当前附件目录中有多份同一录音的 M4A、WebM、WAV、转码及任务副本；音频附件目录也只有这一份用户原始哼唱。没有第二、第三段不同的真人哼唱，因此 SPEC 的“三段真人哼唱”验收尚未完成；同一录音的不同容器格式不能算不同样本。
- 当前结论：Flash 适合确认粗略整体高低走向，但不适合作逐音 MIDI 转录器；Demo 的细粒度音符来自无 ML 的声学测量，云端校验仍是硬性步骤。尚需用其他真人录音检查响度变化、房间噪声及不同哼唱速度对门限与 YIN 稳定性的影响。

## 候选比较：原生音频理解优先

本产品要从哼唱推断旋律音高和音符边界。ASR 识别“说了什么”不能证明模型能分辨音高、同音重唱或停顿，因此 ASR-only 模型不进入主候选。评测问题应给模型音频并要求它逐音输出 MIDI 音高、时间和时值；不告诉歌名、歌词或标准曲谱。

| 候选 | 服务类型及任务贴合度 | 地域、接入与限制 | 结论 |
| --- | --- | --- | --- |
| Klangio Transcription API | 托管音乐转谱 API，明确支持 vocal input，直接返回 MIDI / MusicXML 等；最贴近“人声哼唱→MIDI” | API 文档需申请访问，未公开可确认的价格；德国公司，数据驻留与合规条款需开户前确认 | **功能匹配第一，建议优先申请试用同一录音**；目前不能直接切换，因为无 API 凭据且需核实费用/数据政策 |
| Step Audio R1.1 / Step Audio 2 | 国内云端原生音频理解/推理模型。官方介绍明确列出“理解音乐”，R1.1定位声音理解与推理；不是 MIDI 转谱专用服务 | 有云端 Chat/Realtime API；R1.1 文档标注限免，Audio 2 按音频 token 计费。音乐理解宣传不等于精准逐音转谱，需实测音高和起音 | **原生多模态路线的国内首选待测项**。用 R1.1 先做同录音 A/B；需单独 Step API Key，当前尚未上传录音 |
| Spotify Basic Pitch（本地；Replicate 托管可选） | 轻量、乐器无关的 CNN 音频转 MIDI，官方明确支持 voice/humming，但非人声专项 | 本地 CPU 无需 GPU；Replicate 社区托管 API 约 US$0.0025/次，需独立 token，跨境可达性与录音处理条款需确认 | **真人哼唱已本地实测**：默认参数输出 23 个音符，存在碎音和半音偏差；提高 onset/min-note 阈值可降至 17 音，但音符数减少不能证明旋律更准。保留为转谱基准，不取代云端多模态主流程 |
| Qwen 3.5-Omni / Qwen Omni 系列 | 原生音频多模态，项目已对 Flash、Plus、Turbo、Qwen2.5-Omni 做过有效 WAV 测试 | 百炼北京 API 已可用；此任务上 Plus 和 Flash 实测错音，Turbo/Qwen2.5 也未得到可用结果 | 暂不建议继续在这个系列上花提示词时间 |
| GPT-5.6 Luna（Codex 产品内） | 用户已实测：上传录音后，模型能按预期理解，作为目标任务的质量参照 | 这是 Codex 产品里的音频输入链路；公开 GPT-5.6 Luna API 型号页列出文本/图像输入并注明不支持音频。产品内能力不能直接推断为可调用的同款 API | **保留为效果基准**。后端接入前要确认 Codex 音频附件链路是否有可用的 API 等价接口 |
| ROSVOT | 歌声转 MIDI 专用模型，论文/作者代码直接定位为 singing voice transcription | 目前发现的是开源权重和本机/自行部署推理流程；阿里 PAI EAS 自定义服务会产生独立计算费用 | 任务贴合但违反“不自行部署”的当前要求，先排除 |

**建议下一步：先拿 GPT-5.6 Luna 在 Codex 中的成功结果作人工基准，再用相同音频、相同“只按声音逐音转 MIDI”任务测 Step Audio R1.1。** 同时保留已验证输入链路的 Qwen3.5-Omni Plus 与本地 Basic Pitch 输出作为对照。用已知音高的合成控制音另行评分，但不给模型看标准音高；重点比较音高轮廓、重复起音、停顿、时值和总覆盖。ASR-only 能力不计入评分。当前没有切换 Demo 默认模型，也没有向新供应商上传录音。

## Spotify Basic Pitch 本地实测（2026-09-15）

- 使用 Spotify `basic-pitch==0.4.0`、Python 3.10、Windows ONNX Runtime CPU 后端；依赖和运行环境隔离在 `data/demo/`，未加入应用 requirements。M4A 先用 FFmpeg 转成单声道 WAV；未向模型提供歌名、歌词或已知曲谱。
- 默认阈值输出 23 个 note events，音符序列仍有短碎切分与半音偏差。基于缓存模型输出调高最短音长/onset 阈值，note count 可降至 17；这会合并/丢弃片段，暂不能据此判定识别精度提高。
- Python API 推理（含导入与初始化）耗时约 4.55 秒；采样到峰值 RSS 约 216 MB，CPU 累计时间约 5.09 秒。音频约 10.5 秒，全程 CPU 推理。
- 本结果证明它能处理本段哼唱并生成 MIDI，但尚未解决本项目的碎音/音高精度问题；仅作离线实验基准，不改变云端理解要求。

## 接入边界与尚缺条件

- 从原生多模态改为云端专用转谱，会改变用户原先指定的模型类型，需要先确认这一方向。
- Replicate 使用独立 API token 和计费；现有百炼 key 不可复用。大陆网络可达性与实际延迟尚未验证。其页面示例成本仅作参考，按输入/运行耗时变化。
- 在阿里云部署开源模型属于 PAI EAS 自定义服务，不是百炼直接切换 model ID；资源单独计费，不能假设现有百炼调用授权已经包含 EAS 部署权限。
- 未切换默认模型、未申请或创建新的付费云资源，未向新供应商上传录音。

## 音高、节奏与 MIDI 复核（2026-09-15）

- 项目负责人确认该真人录音为两句、共 14 个音符。当前从原始 WAV 提取到 14 个事件与一个句界，和人工确认一致。
- 音高使用三种传统声学估计交叉核验：项目 YIN、独立 librosa YIN、librosa pYIN；对 14 个已切分音符的稳定区间，独立 YIN 和 pYIN 各有 14/14 个最近 MIDI 半音与 IR 一致。该结果证明稳定音高估计互相吻合，不证明音符切分本身无误。
- 为排除“连续滑音被切成 14 音”的可能，另看各事件稳定区间内的 pYIN 帧分布：中位 IQR 为 0.10 半音、最大 0.40 半音；中位数 94.1% 的有效帧落在该事件中位音高上下 0.25 半音内。多数区间确有稳定音高平台，支持按独立起音生成音符；局部仍有颤音/滑音。
- 又加了不同原理的谐波频谱估计（多谐波能量寻优），13/14 个最近半音与 IR 一致；唯一分歧是第 6 音，三种连续估计分别为 MIDI 54.461、54.492、54.500，恰在 54/55 半音取整分界附近，IR/YIN/pYIN 选 54，而频谱法因数值略高于边界选 55。它与前一个重复音连续估计相同（约 MIDI 54.48），所以没有仅凭这次极小的数值差把 MIDI 改成 55；该音已明确记为临界、不应声称完全无歧义。
- 为覆盖这个边界风险，新增合成回归：四个带轻微颤音的重复音分别位于 MIDI 54 的 +46 cents 和 +54 cents，检查取整前后应稳定分成 `[54,54,55,55]`，且起点误差小于 20ms。它验证边界两侧会正确分音，不替代真人临界音的听感验收。
- IR 与桌面逐音视图现在额外显示“距半音取整边界”的音分余量；小于 15 cents 的音符在琴键图上加橙色描边并在列表标值。当前录音的近边界音会被直接标出，避免把高周期性置信度误当成音高取整毫无歧义。此诊断不改变选出的 MIDI 音高。
- 节奏单独使用短时频谱正变化量（spectral flux）检查起音，不复用应用里的 RMS 起音门限。14 个事件的频谱峰值与 MIDI 起点中位及最大绝对差均为 10ms；非句间起音间隔中位数 0.585s，句间起音间隔 1.14s，其中前一音后到下一句起点的静音约 0.58s。事件起点与 MIDI 往返解析相差小于 2ms。
- 离线事后对照用户给出的《小星星》上下文，只用于解释误差，不传给任何云模型，也不用于覆盖音频推断：按首音移调后，14 个提取音都在标准简谱最近等音高上下一个半音内，6 个完全相同。演唱中的音高偏差会被保留为录音的实际 F0，不用模板强行纠正。
- 最近复核中尚未取得负责人对完整 MIDI 试听的逐音确认，所以不把程序间一致或 14 音计数一致称为“主观旋律完全验收”。原唱与 MIDI 的 A/B 文件是 `data/demo/pitch-trim-validation/original-vs-midi.wav`；连续基频调试图现在会与 MIDI 音符条叠加显示。
- 在加入取整余量标记后，再从真实 FastAPI 上传路由提交原 M4A（job `7fe72a75ca8f4ba5a71ddf10abda22b0`）；Omni 校验成功，状态 completed，14 音、5.64 秒句界、162 个基频轨迹点，状态 API 只在 melody 子对象中返回轨迹。IR 的 14 个音均有取整余量诊断，6 个低于 15 cents 被标为近边界。MIDI 往返仍为 14 音，起点最大差 0.6ms、时值最大差 0.9ms。该新任务自己的 WAV/IR 独立复核为 YIN 14/14、pYIN 14/14、谐波频谱 13/14、频谱流起点差最大 10ms；全部音频路由此前同版本均返回 HTTP 200。完整机器报告在忽略数据目录 `data/demo/pitch-trim-validation/live-http-audit.json`。
- 更新后的独立复核可复现命令：`data/demo/basic-pitch-env/Scripts/python.exe docs/validate_pitch_trackers.py --audio data/demo/7120983acb5a42b49e3de811ed877b16/audio_16k.wav --ir data/demo/pitch-trim-validation/melody_ir.json --out data/demo/pitch-trim-validation/independent-pitch-check.json`。脚本与报告都只用于离线验证，不进入运行时推理链路。

## 一手来源

- [Spotify Basic Pitch 官方仓库](https://github.com/spotify/basic-pitch)：直接 audio-to-MIDI、音高弯曲、单一乐器更适用。
- [Replicate 社区托管 Basic Pitch API](https://replicate.com/rhelsing/basic-pitch/api/learn-more)：需要独立 API token。
- [Replicate 固定版本输入输出](https://replicate.com/rhelsing/basic-pitch/versions/a7cf33cf63fca9c71f2235332af5a9fdfb7d23c459a0dc429daa203ff8e80c78/api)：audio_file 输入，MIDI 文件 URI 输出。
- [ROSVOT 作者实现](https://github.com/RickyL-2000/ROSVOT/blob/main/README.md)：歌声转 MIDI、权重、可选词边界和推理依赖。
- [阶跃声音理解模型](https://platform.stepfun.com/docs/zh/guides/models/realtime)、[当前语音模型列表](https://platform.stepfun.com/docs/zh/guides/models/audio)。
- [阶跃官方价格与限速](https://platform.stepfun.com/docs/zh/guides/pricing/details)：列出 Step Audio R1.1 / Audio 2 计费说明。
- [Klangio Transcription API](https://api-docs.klang.io/)：官方列出 vocal transcription 和 MIDI 导出；文档通过 Request Access 开通。
- [OpenAI GPT-5.6 Luna API 型号能力](https://developers.openai.com/api/docs/models/gpt-5.6-luna)：文本与图像输入；Audio 不支持。
- [阿里云 PAI EAS 自定义部署](https://help.aliyun.com/zh/pai/model-deployment)：需要部署在线服务并支付资源费用。


## MIDI 语义风格转换（2026-09-16）

当前实现把真实 14 音符 canonical MIDI 交给百炼 Qwen Flash 文本模型（可通过环境变量切换 Plus），分别请求 Funk 和 Lofi 的完整风格 MIDI。canonical MIDI 只作为音乐种子，Prompt 允许模型重写音高、时值、时序、和声、低音、鼓组和段落，要求保留核心动机与乐句顺序，不再强制逐音复制。服务端只校验 JSON 结构、音符范围和时值范围，随后由确定性渲染器生成 WAV。

此前的多轨试验确实返回过 lead/bass/harmony/drums，但“保留 lead 再加伴奏”的强约束会让结果听起来接近原 MIDI，已从主流程和验收标准中移除。当前 schema 允许模型自由创作多轨风格 MIDI，由 `tests/test_midi_style.py` 覆盖基本结构校验。

同日用真实录音重新跑旧版强约束 HTTP 主流程，任务 `034e5b5bb0274f3d97492f857380d2c5` 成功完成。Funk 返回 `funk_guitar`、`funk_bass`、`funk_drums` 三轨（14/28/28 音符）；Lofi 返回 `lofi_piano`、`lofi_bass`、`lofi_drums`、`lofi_pad` 四轨（14/7/16/2 音符）。该结果证明链路可用，也暴露出“主旋律逐音不变”会造成听感相似；放宽后的创作 Prompt 需要重新试听评估。

放宽 Prompt 后再次用同一真实录音测试任务 `3f112cdd7f464c6ea4de0466572ea961`：Lofi 返回 `lofi_lead`、`tape_bass`、`swung_drums`、`chord_stabs` 等风格声部（14/9/32/20 音符），允许模型自由组织节奏与配器；Funk 请求本次超过云端响应时限而失败，不能算完整通过。该结果说明放宽约束后模型确实能生成更丰富的 MIDI，但仍需处理单个风格请求的超时与重试策略。

超时复盘发现，风格适配器使用原始 HTTP `httpx`，却把百炼扩展参数写成了 SDK 专用的 `extra_body` 嵌套字段，可能导致 `enable_thinking=false` 没有生效。已改为 HTTP 请求体顶层字段，并将 `max_tokens` 改为 3072、使用紧凑音符数组；这与百炼兼容接口的原始请求格式一致。

随后用最小请求做 A/B：`qwen-plus` 在完整 Funk MIDI Prompt 上仍可能超过 180 秒，而同样的 `qwen-flash` 请求约 5.7 秒返回 3 条风格轨。Demo 默认改为 `qwen-flash`，保留 `QWEN_TEXT_MODEL` 环境变量用于人工对比 Plus；这属于响应稳定性取舍，不改变 MIDI 语义输入和输出格式。

本轮为改善听感将 Prompt 调整为“完整段落重编”：模型先分析输入动机、音程轮廓、乐句和停顿，再生成 `lead_reimagined`、和声、bass 及可选鼓组；主奏允许改写音高、时值、切分和音区，但禁止逐音复制原 lead，也禁止只换 program 或只生成两小节循环。每条声部允许 8–32 个音符，使用紧凑数组格式，`max_tokens=3072` 以避免多轨 JSON 被截断。

对真实 10.5 秒录音做直接 A/B，Funk 约 18 秒返回 4 轨/128 音，Lofi 约 19 秒返回 4 轨/106 音；Funk 主奏 33 音、11 个不同音高，Lofi 主奏 21 音、11 个不同音高，两个结果的覆盖时长分别约 13.75 秒和 15 秒，不再是原先的两小节循环。随后通过完整 FastAPI 上传链路重新跑 job `3eedb3f1c30e4eef877a48eacf430f4a`，约 30 秒完成两种风格，Funk/Lofi WAV 和 MIDI 均成功生成。该机器指标只能证明结构变丰富、链路完成；“眼前一亮”仍需负责人实际试听确认。

复测发现发送给模型的 source JSON 原先遗漏 `duration_seconds`，导致 Plus/Flash 偶尔只编写前半段。已补入原始时长，并保留 3072 token 上限。修正后的 Flash A/B：Funk 约 29.5 秒返回 4 轨，主奏 33 音、覆盖 9.79 秒；Lofi 约 20 秒返回 4 轨，主奏 12 音、覆盖 9.70 秒，鼓组和和声覆盖到约 10 秒。两种结果都没有与原始 14 音逐项相同的声部，且不再退化为 2 秒循环。


为提升最终试听辨识度，程序化渲染器增加了 GM 鼓音色（底鼓、军鼓、踩镲/镲片）、Funk 的短延迟以及 Lofi 的低通和磁带式回声。它们只作用于模型已经生成的 MIDI，不会新增模型轨道或改变 MIDI 语义；最新 job 7be275d9a1e248e4aafaa7b3c0411c91 的 Funk/Lofi WAV 已按新版渲染器重渲染。


最新提示进一步要求和声至少两次色彩变化、bass 至少三个音高并加入经过音、鼓组包含不同打击类型，并禁止整段单音/同一网格。追加 A/B 仍由 Flash 在 24.6 秒（Funk）和 9.5 秒（Lofi）内返回：Funk 主奏 36 音/12 音高、和声 20 音/4 音高、bass 39 音/3 音高、鼓 39 音/2 音高；Lofi 主奏 15 音/5 音高、和声 12 音/12 音高、bass 16 音/3 音高、鼓 13 音/2 音高。该轮尚未替换现场 job，需通过页面新提交后再试听。

渲染器后续又补充了钢琴/键盘击弦包络、主奏合成器的锯齿波与轻微 vibrato，以及 Lofi 极低电平磁带底噪；这些处理仍是 MIDI 到 WAV 的确定性渲染，不改变模型编曲内容。

最终现场复测 job `aa51442e8ad14de0b51df0c33632620e` 在约 45 秒完成。Funk 主奏 37 音/16 音高、和声 20 音/6 音高、bass 39 音/3 音高、鼓 39 音/2 音高；Lofi 主奏 18 音/10 音高、和声 10 音/4 音高、bass 16 音/3 音高、鼓 24 音/2 音高。两种 WAV 均约 10.7 秒，说明质量约束和新版渲染器在真实 HTTP 链路上生效。

最新渲染器还加入了最终峰值归一化（目标峰值 0.78），解决手机播放时音频整体过小的问题；新版 job 的 Funk/Lofi RMS 约 0.21/0.23，峰值均 0.78，且没有削波。

对新版渲染器做了盲听复核：Omni 将 Funk 判断为“偏向 Funk，但仍带复古电子配乐感”，将 Lofi 判断为“最接近 Lofi，氛围慵懒放松”。相较上一版两者都被判断为 Chiptune，这证明和声展开、鼓组音色、主奏波形和母带处理确实改善了风格辨识度；仍存在合成器感偏重的主观风险，需负责人试听决定是否继续换更真实的音色。

再次盲听复核（同一最新 job 重渲染）：Omni 将 Funk 判断为最接近 Funk，将 Lofi 判断为最接近 Lofi；两者不再被归为普通钢琴或 Chiptune。Funk 仍被描述为节奏平稳、合成器感偏重，Lofi 仍有旋律重复，因此当前剩余风险是主观的真实乐器质感和律动变化，而不是 MIDI 是否生效。

尝试把 WAV 改为短延迟立体声宽化后，盲听反而更偏复古电子/游戏配乐，已撤回并恢复单声道；该实验不进入当前版本。

为抑制 Funk 的随机跳音，风格模型默认创作温度设为 0.85，Funk 单独封顶 0.75；Lofi 保持 0.85。该设置在真实调用中返回完整时长和多轨结构，Funk 主奏约 32 音/12 音高，Lofi 主奏约 18 音/8 音高。结构化输出仍使用 3072 token 上限。

为应对 Flash 偶发的长 JSON 截断，风格适配器现在对“可解析 JSON”错误做一次压缩重试（传输/API 超时仍立即失败）；结构正常但音乐性不足时，会根据主奏覆盖、音高变化、Funk 切分和鼓组多样性触发一次纠正重试。该重试只在必要时发生，避免无条件增加延迟。

为降低通用合成器感，rrangement_to_midi 现在按目标风格将模型的 lead_reimagined 映射到 General MIDI 电吉他族（Funk）或电钢琴族（Lofi），再由渲染器使用对应包络；这是音色选择，不保留原始旋律轨，也不新增模型声部。盲听分类对合成器音色仍有波动，因此最终以负责人试听为准。

## 节奏锚定与风格对比修正（2026-09-16）

上一轮现场试听暴露出两个问题：Flash 偶尔把主奏移到 0 秒并压掉录音开头静音/句间停顿；Funk 与 Lofi 虽然 MIDI 结构不同，但合成音色和律动差异还不够明显。本轮做了两处修正：

- `anchor_style_plan()` 在模型返回后执行。它把模型主奏的创作音高按音乐进度放回原始 IR 的每个起音和时值，保留原始开头、结尾和可测停顿；伴奏则按源时长做时间映射，并在源停顿处裁切。模型仍然负责主奏音高、和声、低音、鼓组和段落，未把原 lead 音符复制回风格轨。
- Prompt 改为将 `rhythm_template` 明确为不可破坏的节奏骨架，同时放宽音高、装饰音和配器创作。Funk 请求更强调反拍、幽灵音、律动低音和 fill；Lofi 请求更强调 swing、七/九和弦、留白和磁带空间。MIDI 序列化时 Funk 映射为电吉他/Clav、slap bass、Clav 和强鼓；Lofi 映射为电钢琴、warm pad、finger bass 和低密度鼓。渲染器只对伴奏/鼓做 pocket swing，主奏保持原始起音。

用真实 175287-byte M4A 通过 HTTP 新跑 job `8ee2efa27e6e463e9cd75fbbafe2b690`：原始 IR 为 14 音、102.56 BPM、句界 5.64 秒；Funk/Lofi 主奏均为 14 音且逐项起音/时值与 IR 相同，分别生成 75/57 个多轨 MIDI 音符，音频路由均 HTTP 200。Funk 主奏保留先升后降轮廓（52,52,57,57,60,60,57…），Lofi 为另一套风格化音高（57,57,62,64,66,65,63…），不再与原 MIDI 字节相同。该机器验证证明节奏锚定和风格差异已进入运行链路；最终“是否惊艳”仍以负责人手机试听为准。

随后固定风格 MIDI 的 `tempo_bpm` 回到源值，避免模型把熟悉旋律整体加速/减速。真实 M4A 再跑 job `70282d6363e143a2b302b88c9f78f31e`：两种风格均为 102.56 BPM，主奏均为 14 音且起音/时值逐项等于 IR（1.04 秒起、5.64 秒句界、9.79 秒收束）；Funk/Lofi 分别为 99/59 个多轨音符，音频均 HTTP 200。渲染后的平均频谱重心约 613Hz（Funk）与 296Hz（Lofi），源停顿中段（5.3–5.5 秒）RMS 约 0.0001 与 0.0029，说明两种母带处理确实不同且停顿仍可听见。Lofi 的 tape hiss 已降到极低电平，避免掩盖句间呼吸。该结果是当前现场试听候选。

加入“逐音复制兜底”和低噪声修正后，最终真实 M4A job `9d87000caeff45d9918eb379ad9bff03` 完成：Funk/Lofi 都是 102.56 BPM，主奏都是 14 音，起音和时值最大误差均为 0，且 14/14 个主奏音高都经过风格化重写；两种主奏的升降轮廓与原始序列完全一致。Funk/Lofi 多轨 MIDI 分别为 74/55 个音符，音频路由均 200。这个结果同时满足“听得出原哼唱节奏”和“风格 MIDI 不再复制原 MIDI”的机器验收条件。

对该 job 的最终 WAV 又加入 Funk 轻微高频激励（拨弦/军鼓瞬态），以扩大与 Lo-fi 低通母带的音色距离；MIDI 内容未改变。重渲染后的平均频谱重心约为 Funk 506Hz、Lofi 309Hz，句间停顿中段 RMS 约 0.0001、0.0029，峰值均归一到 0.78。服务重启后健康检查仍返回 `ok|procedural-midi-synth|qwen-flash`。

盲听仍提示 Funk 鼓组不够明确，因此在 `anchor_style_plan()` 中只补齐模型缺失的 Funk 反拍军鼓、晚八分踩镲和少量 ghost hit，不触碰主奏节奏；原模型鼓点和其他声部全部保留。最终 job 的 Funk 鼓轨从 32 个事件补到 67 个事件，包含 kick/snare/hat 三类 GM 打击音，Lo-fi 仍保持模型给出的低密度鼓轨。合成器的鼓噪声改为确定性的白噪声近似，降低周期性电子音色；服务已重启并通过 16 项单元测试。

在 Lo-fi 长和弦成形后又发现延音跨越句间停顿，已加入停顿裁切并新增回归测试。当前真实 M4A job `e6651b913a304e30b3c4cf82d59cc19d` 完成：Funk/Lofi 主奏均 14 音、起音和时值最大误差 0，14/14 音高均被重写，BPM 均为 102.56；Funk 139 音（28 harmony、28 bass、69 drums），Lo-fi 44 音（7 harmony、7 bass、16 drums），两条音频均 HTTP 200。Funk 的伴奏反拍与 Lo-fi 的长和弦/低密度鼓组已在 MIDI 层分离，句间停顿没有被长音填满。

渲染器最后将 Funk harmony 的 GM program 7 独立处理为短促 Clav 包络，与 Funk 电吉他/Slap bass 和 Lo-fi 电钢琴/Warm pad 分开；该改动只影响 MIDI 到 WAV 的音色，不影响主奏节奏或模型输出结构。当前 8000 端口已重启并加载此实现，最新试听链接仍指向上述 job。

随后将 Funk 电吉他加入确定性的 Karplus–Strong 拨弦反馈，并重新平衡两种总线：Funk 鼓组/低音前景更高，Lo-fi 暖和声保留低密度和低通。`e6651b913a304e30b3c4cf82d59cc19d` 的 WAV 已重渲染，最新 8000 服务已加载该渲染器；MIDI 文件本身没有变化。

对 `e6651b913a304e30b3c4cf82d59cc19d` 的最终 Funk/Lo-fi WAV 做了同一套 Qwen Omni 盲听复核：模型仍倾向描述为复古电子游戏/电子配乐，未稳定识别出 Funk 或 Lo-fi。这是通用音频模型对极简确定性合成的听感偏差，也是当前剩余的主观风险；它不能推翻 MIDI 层已经验证的风格差异和节奏锚定。若负责人试听仍觉得电子感过重，下一步应更换真实采样/音源渲染器，而不是继续收紧 MIDI Prompt。

## SoundFont 渲染补齐（2026-09-16）

为解决上述电子感问题，新增 `app/audio_renderer.py`：优先调用 FluidSynth 2.6.0 的 SoundFont 渲染，输出统一转换为 22.05 kHz 单声道 WAV；Funk 仅做轻微饱和/瞬态处理，Lofi 做低通/回声，MIDI 的 program、力度、鼓组和模型编曲保持不变。`AUDIO_RENDERER=auto` 自动选择，`AUDIO_RENDERER=soundfont` 用于验收时强制检查音源是否就绪，`procedural` 可复现旧版对照。

本机已下载并实测 `GeneralUser-GS.sf2`：对 `e6651b913a304e30b3c4cf82d59cc19d` 的 Funk MIDI 直接渲染成功，FluidSynth 输出有效 WAV；服务健康检查现在报告 `fluidsynth-soundfont`。音源和 FluidSynth 二进制均放在被忽略的 `data/` 目录，不进入仓库提交。安装和许可证注意事项见 [`docs/SOUNDFONT.md`](SOUNDFONT.md)。

演奏层与动态实验现场复测（2026-09-16）：真实 M4A 新 job `47401e1804974ddfae21b59d0b64c0b9` 完成，canonical MIDI 仍为 14 音、102.56 BPM，Funk/Lofi 风格 MIDI 分别为 106/48 音符；两个 WAV HTTP 200，API `renderers` 明确报告均为 `fluidsynth-soundfont`。新增 CC7/10/91/93、固定 seed 微时差/力度和总线能量弧线后，音频峰值均归一到 0.82。该轮证明新演奏层已进入真实端到端链路，主观“惊艳度”仍需手机盲听。

响度修复（2026-09-16）：线上 SoundFont WAV 的原始平均电平约 -18.7/-16.8 dB，峰值 -1.7 dBFS；确认是动态范围偏大。新增轻度总线压缩与补偿增益，峰值目标 -0.7 dBFS。生产机已重启并重渲染 `fe96c26e463d472c857ddf76c69a8398`，Funk/Lofi 平均电平升至 -16.7/-15.8 dB，音频接口仍为 200。

## 发散式转谱评审（2026-09-17）

这次按 review 重构了转谱边界：`app/transcription.py` 定义了小型 `TranscriptionEngine` 协议，当前有可重复的 `dsp-yin` 基线和可选的云端 `klangio-vocal-cloud` 实现。主流程通过 `TRANSCRIPTION_ENGINE` 选择引擎；`auto` 只有在检测到 `KLANGIO_API_KEY` 时才选择 Klangio，云端失败会直接暴露，不会偷偷切回 DSP。这样一次 A/B 的结果可以归因到实际模型，而不是被隐式 fallback 污染。

针对“通用多模态是否能替代专业转谱”的问题，做了四组真实录音实验：

| 路线 | 模型/方法 | 结果 | 结论 |
| --- | --- | --- | --- |
| A | Qwen Omni 直接输出完整 MIDI JSON | 音符数有时接近，但出现固定重复音、等间隔时值 | 不能作为逐音转谱引擎 |
| B | Qwen Omni 只在相邻 ±1 半音候选中选择 | `docs/probe_multimodal_note_ranking.py` 的随机候选仍有位置/极值偏置，多个音未选中真实音高 | 音频理解能力存在，但不适合当音高判别器 |
| C | YIN、Basic Pitch、pYIN、Praat 交叉测量 | 四条路线的整数音高轮廓基本一致；pYIN/Praat 没有减少这段录音的错误，且 pYIN 首次编译很慢 | 当前 DSP 结果有独立交叉证据，剩余误差主要是人声滑音和半音量化 |
| D | Klangio `vocal` 云端专用转谱模型 | 已完成 provider adapter，尚未配置该服务密钥，不能把“未实测”写成通过 | 这是下一项真正有区分度的云端 A/B |
| E | 腾讯多媒体实验室 `vocalMidi` | 已完成异步任务 provider adapter；需要腾讯 SecretId/SecretKey 和 HTTPS 音频 URL，尚未配置凭据 | 大陆可用性和人声转 MIDI 定位更贴近本项目，优先于继续调 Qwen Prompt |

Klangio 的官方 API 明确提供 Vocal 转谱模型、MIDI 输出和异步任务流程：[API 总览](https://api-docs.klang.io/)、[基础任务流程](https://api-docs.klang.io/docs/getting-started/basic-job-workflow)、[转录模型选择](https://api-docs.klang.io/docs/advanced-usage/transcription-model-selection)。它是面向音乐转谱的服务，和 Qwen Omni 的通用音频理解定位不同；是否更准必须用同一份录音实测后再决定。

腾讯多媒体实验室的官方文档也明确写出“人声转录”会计算每个音符的音高和区间并输出 MIDI/JSON，并提供 `CreateJob`/`GetJob` 异步任务流程：[Vocal To Midi](https://multimedia.tencent.com/en/docs/smart-music/api/10-vocal-to-midi/)、[智能音乐简介](https://multimedia.tencent.com/zh/docs/smart-music/about/introduction/)。该接口要求第三方通过 HTTPS URL 读取输入，因此代码增加了按随机 job id 暴露的临时规范化 WAV 路由；密钥和服务器本地路径不会出现在 API 响应或日志中。

当前已做的音准保真处理是：IR 保存 `pitch_cents`，标准 MIDI 写入 ±2 半音 Pitch Bend，并合并尾部同音短碎片；这些处理改善了“整数音名相同但播放偏”的情况，却不能把一段离调哼唱自动纠正成用户脑中的标准曲谱。若验收标准是“还原用户实际唱的音高”，当前 DSP 基线已经有稳定证据；若标准是“猜出用户想唱的标准旋律”，需要提供参考音频/调性或使用专门的云端转谱模型，不能靠继续堆通用 Prompt 保证。

这轮结论不是“问题已经解决”：Qwen Omni 仍保留为整体理解/盲听评测工具，未进入逐音生产路径；Klangio adapter 也未在没有密钥时启用。下一次 A/B 应使用同一原始 WAV，同时记录音符起止误差、整数音高准确率、音分误差、处理时延和费用，再决定是否替换 DSP 基线。

本次重构还移除了“粗略走向不一致就直接失败”的硬门槛：Qwen 的走向判断仍然必做并写入 job，转谱引擎的走向也单独保留；两者不一致时记录 `validation_warning`，继续产出并把冲突交给人工复核。这样通用多模态模型至少能提供可见的独立证据，不会因为一次粗粒度分类抖动而阻断真正的转谱 A/B。

随机候选复测（同一 `model-compare/audio_16k.wav`，音符索引 2、4、8，每个音符重复 2 次，候选顺序随机）结果为：3 个音符共 6 次选择，整数音高命中 `0/6`。模型对索引 2 两次都答 A、索引 4 两次都答 C、索引 8 两次都答 A，但 A/C 对应的候选音高随顺序变化，说明它在这个窄任务上表现为固定位置偏好，而不是稳定的音高比较。这个结果足以把“让通用 Omni 负责逐音校正”从当前候选方案中剔除。

随后做了整段候选实验（`docs/probe_multimodal_melody_ranking.py`）：候选都复用原始人声片段和原始节奏，只改变整段音高序列，并随机打乱候选顺序。3 次重复中模型选择了 `smoothed`、`motif`、`smoothed`，没有一次选择 DSP 实测序列。由于候选本身没有提供标准曲谱，这个实验不能证明 motif 候选正确，但它证明“让通用 Omni 在整段候选中替 DSP 判音”也没有形成可依赖的校正信号。
