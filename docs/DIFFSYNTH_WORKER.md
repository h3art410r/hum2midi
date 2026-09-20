# DiffSynth Worker Agent 运行说明

这份说明是给负责 GPU Worker 部署的 Agent 读取的。Worker 运行在 Windows + RTX 5060 Ti 16GB，开发机 FastAPI 通过 `http://192.168.9.100:8765` 调用它。Worker 只负责加载模型和生成音频，不修改前端、后端任务逻辑或提示词。

## 更新和启动

每次收到“更新 Worker”或“切换显存模式”的任务，先在仓库目录执行：

```powershell
git pull origin main
```

默认安全模式启动：

```powershell
.\scripts\start_diffsynth_music_server.ps1 -Port 8765 -OffloadMode cpu
```

用于速度 A/B 的 DiT 常驻显存模式：

```powershell
.\scripts\start_diffsynth_music_server.ps1 -Port 8765 -OffloadMode dit_cuda
```

`dit_cuda` 是实验模式，16GB 显存可能 OOM；发生 OOM 时停止 Worker，改回 `-OffloadMode cpu` 重启。`none` 会完全关闭 VRAM 管理，只能在明确要求时使用。

## 启动后验收

启动后必须检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health | ConvertTo-Json
```

返回中必须能看到：

- `status: ok`
- `offload_mode` 与本次启动参数一致
- `build` 是刚刚 `git pull` 后的提交短 hash
- `control: control+prosody`
- `templates: control, prosody`
- `device` 是 RTX 5060 Ti

如果 `/health` 没有 `offload_mode` 或 `build`，说明运行的仍是旧代码，不能继续做性能结论。

## 性能日志

生成完成后可读取：

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/debug/logs?since=0&limit=500" | ConvertTo-Json -Depth 12
```

日志包含模板正负分支、Pipeline Unit、模型设备切换、每个 DiT 正负 CFG forward、每个 denoise step、VAE 解码、保存和显存峰值。不要把音频内容、密钥或完整 prompt 写入 Worker 日志。
