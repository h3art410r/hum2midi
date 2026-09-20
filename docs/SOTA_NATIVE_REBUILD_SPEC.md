# Hum2Midi 原生模型重构方案（全新版本）

> 版本边界：这是一个全新的后端版本，不是对现有 Demo 的继续打补丁。

## 0. 全新版本与旧版代码隔离

- 新版本从空的服务骨架开始实现，旧版 `app/`、`remote/`、`scripts/` 和旧测试不作为新版本的代码依赖。
- 新版本不得从旧版模块 import，也不得复制旧版的 MIDI、YIN、Stable Audio、Qwen、历史 prompt、显存 offload 或缓存逻辑。
- 旧版只保留在仓库中作为历史记录和效果对照，不能被新接口隐式调用；旧版任务、旧缓存和旧输出不能冒充新版本结果。
- 新版本使用独立的配置命名、任务目录、模型 Worker 和健康检查。切换版本通过明确的入口完成，不在一个进程里混合两套实现。
- 第一份可运行代码只实现“真实音频输入 → 官方模型推理 → 音频输出”这一条链路，先建立新的质量基线，再逐步增加功能。
- 所有新代码、测试和部署说明必须在本方案定义的版本边界内新增；如果需要参考旧版，必须在设计记录中说明参考原因，不能直接复用实现。

## 1. 文档目的

这是一份面向全新版本后端的独立方案。它不把现有 Demo 的路由、MIDI 流程、历史 prompt、显存魔改、试听实验和兼容分支当成设计前提。

唯一要保留的产品目标是：

> 用户上传一段真实哼唱，系统理解它的旋律与节奏，生成一段完整的风格化音乐，并且让用户仍然听得出原始哼唱的音乐身份。

第一阶段固定生成两个风格：Funk 和 Lo-fi。两个变体共用同一份真实输入和 Prosody 条件，只改变风格 prompt；先把一条稳定、可解释、可重复测试的端到端链路跑通。

## 2. 设计原则

- **模型原生优先**：严格按照模型官方推荐的输入、条件和推理接口调用，不复用旧版的自定义 pipeline 改写。
- **单一责任**：后端负责任务、文件和状态；模型 Worker 负责模型加载与推理；前端负责上传、进度和试听。
- **真实输入**：每次验收都使用用户真实录音，不使用固定旋律、MIDI 模板或 mock 音频伪造结果。
- **可复现**：输入文件、模型版本、参数、随机种子和 prompt 都写入任务元数据。
- **先验证效果，再优化性能**：性能优化不能改变模型的输入语义。任何优化都必须用同一输入做 A/B 对照。
- **不引入 MIDI 中间层**：本版本直接走音频到音频，MIDI 只作为未来独立实验方向，不进入主链路。

## 3. 目标链路

```text
真实哼唱
  -> 音频格式与响度规范化
  -> 模型官方音频条件提取
  -> 原生音频到音频生成
  -> 输出时长和格式校验
  -> 试听与任务诊断
```

输入预处理只做模型官方要求的操作：采样率、声道布局、长度对齐和必要的响度处理。不得在这里做旋律修正、歌曲识别或模板匹配。

## 4. 确定的模型方案

当前新版本确定使用 `DiffSynth-Music` 的官方 Prosody 条件路径。它接收真实哼唱提取出的音频 prosody 条件和英文 prompt，由模型直接生成完整音乐，不把原始哼唱轨叠回输出。

模型调用必须使用官方 `TemplatePipeline` 和官方 Prosody template（`model_id=1`），不改写模型 forward，不加入自定义 MIDI、YIN、重绘锚点或额外伴奏混音。官方示例参数作为新版本的初始基线：`tiled=True`、`cfg_scale=4`、`num_inference_steps=50`、固定 `seed=42`，生成时长取实际 prosody 条件长度。

第一阶段默认只启用 Prosody-only。Control、Reference 和联合条件暂不进入默认链路；在 16GB GPU 上只有经过独立显存实测并确认安全后，才允许作为后续实验启用。

模型权重只部署在独立 GPU Worker，第一阶段目标机器是 RTX 5060 Ti 16GB；FastAPI 后端不加载模型，只通过清晰的 HTTP 模型适配接口调用 Worker。模型版本、权重来源和 Git 构建版本必须写入每个任务的诊断信息。

实现中仍保留一个小而明确的 `MusicModel` 接口，接口的用途是隔离 Worker，而不是提前建设多模型平台。只有当 DiffSynth-Music 无法达到旋律身份门槛时，才另开实验文档评估替代模型，不在新版本中暗中切换。

### 4.2 模型适配接口

```python
class MusicModel:
    name: str
    version: str

    def health(self) -> dict: ...

    def generate(
        self,
        input_audio: Path,
        prompt: str,
        seed: int,
        duration_seconds: float,
        parameters: dict,
        output_audio: Path,
    ) -> dict: ...
```

接口返回模型名、版本、实际输入时长、输出时长、推理耗时、显存峰值和使用的参数。模型不可用时任务必须失败并显示真实原因，不能切换到 mock 或其他未声明模型。

## 5. 官方 Quick Start 与新版本的对应管线

官方文档：<https://diffsynth-studio-doc.readthedocs.io/en/latest/Model_Details/DiffSynth-Music.html>。官方 Quick Start 的共同流程是：加载基础 `DiffSynthMusicPipeline`，加载三个 template 权重，准备某一种控制音频，调用 `TemplatePipeline`，最后以 48kHz 保存输出。

官方 Prosody-only 管线的等价最小代码如下。代码保留官方调用语义，省略服务端队列和 HTTP 封装：

```python
import torch
import torchaudio
from diffsynth.core.data.operators import LoadMultiTrackAudio
from diffsynth.diffusion.template import TemplatePipeline
from diffsynth.pipelines.diffsynth_music import DiffSynthMusicPipeline, ModelConfig
from diffsynth.utils.music_tools import extract_prosody

model_id = "DiffSynth-Studio/DiffSynth-Music"

pipe = DiffSynthMusicPipeline.from_pretrained(
    torch_dtype=torch.bfloat16,
    device="cuda",
    model_configs=[
        ModelConfig(model_id=model_id, origin_file_pattern="transformer/model.safetensors"),
        ModelConfig(model_id=model_id, origin_file_pattern="conditioner/model.safetensors"),
        ModelConfig(model_id=model_id, origin_file_pattern="text_encoder/model.safetensors"),
        ModelConfig(model_id=model_id, origin_file_pattern="vae/model.safetensors"),
        ModelConfig(model_id=model_id, origin_file_pattern="track_separator/model.safetensors", computation_dtype=torch.float32),
    ],
    tokenizer_config=ModelConfig(model_id=model_id, origin_file_pattern="text_encoder/"),
)

template = TemplatePipeline.from_pretrained(
    torch_dtype=torch.bfloat16,
    device="cuda",
    model_configs=[
        ModelConfig(model_id=model_id, origin_file_pattern="template_control/"),
        ModelConfig(model_id=model_id, origin_file_pattern="template_prosody/"),
        ModelConfig(model_id=model_id, origin_file_pattern="template_reference/"),
    ],
)

audio = LoadMultiTrackAudio(division_factor=3840)("input.wav")
prosody = extract_prosody(audio)
result = template(
    pipe,
    prompt="An energetic instrumental funk track with a strong bass groove.",
    negative_prompt=pipe.default_negative_prompt,
    lyrics="",
    duration=prosody.shape[1] / 48000,
    seed=42,
    tiled=True,
    cfg_scale=4,
    num_inference_steps=50,
    template_inputs=[{"model_id": 1, "audio": prosody}],
    negative_template_inputs=[{"model_id": 1, "audio": prosody}],
)
torchaudio.save("output.wav", result, 48000)
```

`prompt` 描述希望生成的音乐，`negative_prompt=pipe.default_negative_prompt` 提供文本条件的负向分支。采样时 CFG 会比较正向和负向预测，放大正向 prompt 相对于负向 prompt 的差异，从而减少默认负向描述中的不良音乐特征。它不是在否定用户的哼唱，也不是音频降噪；第一阶段直接沿用官方默认负向 prompt。

`negative_template_inputs` 是另一套机制：它给 Prosody 音频条件提供负向分支。官方示例把同一份 prosody 条件同时传给正、负模板输入，这是模板 CFG 的标准写法，不能与文本 `negative_prompt` 混为一谈。

### 5.1 哼唱输入的适配

官方 Prosody 示例对完整歌曲先调用 `pipe.extract_track(..., track="vocals")`，再提取 Prosody。我们的输入本身就是人声哼唱，因此新版本不做 Demucs vocal 分离，也不调用 `extract_track`：

```python
audio = LoadMultiTrackAudio(division_factor=3840)("hum.wav")
prosody = extract_prosody(audio)
```

其余 `TemplatePipeline` 参数和 `model_id=1` 保持不变。输入先变成官方要求的 48kHz 音频，并按 `division_factor=3840` 对齐；如果输入是单声道，官方加载器会复制成两个声道。新版本对手机录音的双声道情况先选择有效声道，再复制成两个相同声道，避免把一条有效声道和静音声道平均。

这里的“把人声重合成为正弦波”是工程侧的 `extract_prosody` 预处理，不是生成模型内部隐式完成的步骤。工程侧先用 pYIN 估计基频、从音频包络得到幅度轨迹，再用正弦载波重建一条只表达音高和时间的条件波形；模型收到的是这条已经重合成的条件波形。这样可以在进入模型前主动去掉大部分歌词、发音和原始音色线索。

### 5.2 Quick Start 中没有使用的路径

- 不使用 `input_audio` 和 `denoising_strength`；它们属于基础 pipeline 的 audio-to-audio 重绘参数，不是官方 Prosody template 的控制路径。
- 不使用 `target_audio` / `target_track`；这会把输入轨融合回输出，与“完整风格化、不保留原始哼唱轨”的目标不同。
- 不使用 Control、Reference 或联合条件；它们保留在后续独立显存实验中。

## 6. Prompt 初始版本

模型收到的两个 prompt 先保持短而明确，避免用文字重述旋律，让音频条件承担旋律和节奏约束：

官方没有 Funk 或 Lo-fi 的内置正向 Prompt。官方 Quick Start 只提供一个通用音乐描述示例；下面两段是项目自己的风格配置，不应标记为模型默认值。模型默认值只存在于 `pipe.default_negative_prompt`，负向分支按 6.1 节原样读取。

```text
An energetic instrumental funk track with a strong bass groove,
syncopated drums, rhythmic guitar, tight keyboard accents,
and a memorable arrangement.
```

```text
A warm, laid-back lo-fi instrumental with dusty drums, mellow keys,
soft bass, subtle texture, and a memorable arrangement.
```

后续只允许一次修改一个变量：prompt、seed、CFG、步数、音频条件强度或输入预处理。每个实验都要保存输入和输出，不能凭印象混合比较。

### 6.1 负向 Prompt 的来源与生成规则

`negative_prompt` 不由另一个大模型现场改写，也不根据 Funk 或 Lo-fi 的名称随意编写。官方 DiffSynth-Music Quick Start 直接使用 `pipe.default_negative_prompt`；它是随模型代码提供的稳定基线，服务于文本条件的 classifier-free guidance（CFG）负向分支。新后端必须在 Worker 启动时从已加载的 pipeline 读取这个值，并把实际发送的文本和模型版本写入任务诊断，不能把它硬编码成一段可能过期的副本。官方没有对应风格的正向默认值，正向风格词必须单独标记为项目配置。

两个风格在第一阶段共用同一个官方负向 Prompt。这样 A/B 比较只改变正向风格描述，避免负向词同时改变而无法判断效果。负向 Prompt 只用于压制官方默认描述中的低保真、静态噪声、削波、明显失调和旋律不连贯等失败特征；它不应该写成“不要 Funk”“不要 Lo-fi”，也不应该否定 Prosody 需要保留的旋律和节奏。

调用形态固定为：

```python
negative_prompt = pipe.default_negative_prompt

audio = template(
    pipe,
    prompt=style_prompt,
    negative_prompt=negative_prompt,
    lyrics="",
    duration=prosody.shape[1] / 48000,
    seed=42,
    tiled=True,
    cfg_scale=4,
    num_inference_steps=50,
    template_inputs=[{"model_id": 1, "audio": prosody}],
    negative_template_inputs=[{"model_id": 1, "audio": prosody}],
)
```

这里有两种不同的负向输入：文本 `negative_prompt` 是文字 CFG 分支；`negative_template_inputs` 是 Prosody 模板的音频 CFG 分支，官方示例把同一份 Prosody 条件传给正、负两侧。第一阶段两者都按官方写法执行。只有在固定输入、固定种子和官方参数下确认某一类可重复的音频伪影后，才允许新增一条很短的实验性负向后缀；每个后缀必须单独记录并与官方默认值盲听对照，不能直接替换生产基线。

## 7. 论文原文复核

原文：<https://arxiv.org/abs/2609.12774>；官方实现说明：<https://diffsynth-studio-doc.readthedocs.io/en/latest/Model_Details/DiffSynth-Music.html>。

### 6.1 模型真正做的事情

论文的骨干是冻结的 ACE-Step-1.5-XL-SFT DiT。DiffSynth-Music 没有重新训练整个生成器，而是训练三个模板模块：Control、Prosody 和 Reference。每个模板把条件音频的 latent 变成每个注意力层的 key/value，生成分支在注意力中把这些条件 KV 和自身 KV 拼接起来，再预测 flow-matching 的 latent 速度场。

因此，模型的核心不是“输入音频加一点效果”，而是“用音频条件改变生成 DiT 的注意力记忆”。生成仍然是新的音乐，原始哼唱不会作为一条源轨直接混回输出。

### 6.2 Prosody 条件的含义

论文中的 Prosody 处理流程是：对输入人声做通道平均，用 pYIN 在 65–1000Hz 范围估计基频，使用 512 样本 hop，对缺失基频做填补和插值；同时对绝对值包络做零相位低通滤波，再用正弦载波重合成。最后把单声道结果复制到输出声道。

这会保留音高轨迹和时间包络，但主动削弱发音、歌词和原始音色。它适合让生成音乐“沿着这段旋律走”，不等于逐帧复制原始哼唱的起音、音节和演唱表现。

### 6.3 三类条件的分工

- **Control**：论文把 beats、vocals、accompaniment 放在这个模板中。它们是时间对齐的条件，其中 vocals 更接近旋律和演唱表现，beats 更直接表达节拍事件。
- **Prosody**：表达音高和时间，减少语言和音色线索，适合把哼唱变成器乐旋律。
- **Reference**：从最响的连续片段取参考，表达整体风格、音色和制作质感，不负责把事件对齐到输出时间轴。

这些条件在推理时组合的是 KV memory，不是把多条波形直接相加。论文明确允许联合条件；但官方示例只使用 Prosody，因此新版本先以 Prosody-only 建立干净基线，再单独验证 Control + Prosody，不能把两种结果混称为官方基线。

### 6.4 论文实验对我们的限制

论文实验使用 100 段带歌词的中英文歌曲，并用歌曲分离、节拍提取和人声重合成构造控制条件。它报告 Prosody 的 `Pitch50` 从骨干模型的 0.0366 提升到 0.4671；这些结果证明条件机制有效，但不等于模型已经针对手机哼唱优化。我们的输入没有歌词、没有完整伴奏，属于不同的输入分布。

论文的官方推理基线是 CFG 4、50 steps，输出时长匹配测试样本。这就是新版本的第一组质量基线。任何更快的配置都必须和这组基线用同一输入做盲听对照。

### 6.5 对新版本的直接结论

1. 不能用更长的文字 prompt 补偿缺失的音频控制；prompt 负责风格，音频条件负责旋律和时间。
2. 如果 Prosody-only 听不出哼唱节奏，下一项合理实验是按论文定义加入 Control 条件，而不是修改 MIDI 或把原始音频混回输出。
3. Reference 只用于风格/音色实验，不能用来修复旋律和节奏。
4. 任何显存或速度优化都必须保留模板 KV 的计算和复用语义；不能为了省显存重写模板 forward 或改变条件注入位置。

### 6.5 共享 Prosody 条件的架构决策

论文把模板音频编码成每层的 KV memory，并在采样期间复用这份 memory；官方 pipeline 也公开了 `kv_cache` 和 `negative_kv_cache` 参数。因此一次任务应该先把输入规范化、`extract_prosody` 和正负 Prosody 模板缓存做完，再用同一对缓存分别生成 Funk 和 Lo-fi：

```text
真实哼唱
  -> 官方输入规范化
  -> extract_prosody（只做一次）
  -> Prosody 正/负模板 KV cache（只做一次）
       ├─ Funk prompt  -> pipe(kv_cache=..., negative_kv_cache=...)
       └─ Lo-fi prompt -> pipe(kv_cache=..., negative_kv_cache=...)
```

两个变体仍然顺序运行，共用一个常驻模型实例，避免 16GB 显存同时保留两套采样状态；缓存只在当前任务期间存在，两个结果结束后释放。两次生成首轮使用相同 seed、CFG 和 steps，确保差异主要来自风格 prompt；前端按变体独立更新，先完成的结果先展示。这个封装只使用官方公开的 KV 输入，不修改 DiT forward、模板注入位置或负向 CFG 语义。

## 8. 后端结构

### 6.1 API 服务

Python + FastAPI，提供：

- `POST /api/generations`：上传真实音频并创建任务。
- `GET /api/generations/{id}`：查询任务状态和诊断信息。
- `GET /api/generations/{id}/audio/{variant}`：读取生成音频。
- `GET /api/generations/{id}/source`：读取规范化后的输入。
- `GET /api/health`：报告后端和模型 Worker 状态。
- `GET /api/debug/logs`：查看阶段日志。

一次任务包含 `funk` 和 `lofi` 两个独立变体。后端必须在每个变体完成时立即更新任务状态和音频 URL；前端轮询到任意一个 `completed` 变体后就立即展示该结果，不等待另一个变体。只有两个变体都完成或明确失败后，任务才进入最终状态。

API 服务不加载 GPU 模型。它通过内部 Worker 客户端提交音频和参数，并保存任务元数据。

### 6.2 GPU Worker

GPU Worker 是常驻进程，负责：

- 启动时加载模型并报告健康状态；
- 接收一次生成请求；
- 按官方路径执行条件提取和采样；
- 保存 WAV 输出；
- 返回模型版本、耗时、显存和阶段日志。

Worker 不负责网页、不负责任务历史、不负责 prompt 预设管理。Worker 更新由守护进程完成，但守护进程不能改变模型调用语义。

### 6.3 文件与任务

每个任务目录包含：

```text
source.<original-ext>
input-normalized.wav
request.json
1.wav
diagnostics.json
```

`request.json` 必须记录模型版本、prompt、seed、CFG、步数、输入采样率、输入声道、实际生成时长和 Git 构建版本。

## 9. 质量验收

每次实验用同一段真实哼唱，至少评价两件事：

1. **旋律身份**：前半段和后半段是否都能听出来自同一段原始哼唱。
2. **风格完成度**：Funk 和 Lo-fi 是否分别成为完整编曲，而不是原始哼唱加一条伴奏。

性能、频谱相似度和响度只能作为诊断数据，不能替代试听结论。

最低通过条件：

- 输出是非空、可播放的 WAV；
- 输出时长与输入相近；
- 输出没有原始人声和明显底噪泄漏；
- 用户能在完整音频中辨认出原始旋律和节奏；
- 模型不可用时前端显示明确失败，而不是返回旧缓存冒充新结果。

## 10. 实验方法

建立一个固定输入试听页，使用同一真实录音重复生成。每轮只改变一个变量，并记录：

- 模型与权重版本；
- 官方输入预处理是否完整执行；
- prompt、seed、CFG、steps；
- 输入条件强度或重绘参数；
- Worker 阶段耗时和显存；
- 用户对旋律身份和风格完成度的听感评分。

如果某次效果明显变好，即使更慢，也先冻结为质量基线，再单独做性能 A/B。性能版本不得直接覆盖质量基线。

## 11. 第一阶段实施顺序

1. 创建干净的模型适配模块和 Worker API。
2. 按官方示例实现最小单任务推理，并为 Funk、Lo-fi 建立两个变体。
3. 用一段真实录音完成离线 Worker 验证。
4. 接入 FastAPI 任务状态和试听页面；任何先完成的变体立即可试听。
5. 固定质量基线，再开始性能和显存实验。
6. 只有当第一候选模型无法达到旋律身份门槛时，才引入第二个模型做对照。

## 12. 明确不做的事情

- 不把旧版代码逐段搬进新后端。
- 不在主链路加入 MIDI、YIN、歌曲识别或外部曲谱检索。
- 不把原始哼唱或伴奏轨直接混回生成结果。
- 不为了速度关闭官方条件、替换官方 pipeline 或修改模型内部 forward。
- 不把缓存音频当成新任务的成功结果。
