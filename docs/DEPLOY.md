# Native Demo 部署记录

目标公网机：`ubuntu@kr.sunyongfei.cn`。它只负责 HTTPS、Nginx 和反向隧道，不下载模型，也不执行 DiffSynth 推理。

## 当前拓扑

```text
https://kr.sunyongfei.cn/hum2midi/
        -> 公网 Nginx : 18000
        <- SSH reverse tunnel <- 开发机 native.api : 8000
        -> 局域网 HTTP -> RTX 5060 Ti Worker : 8765
```

公网机的应用目录是 `/home/ubuntu/hum2midi`，Nginx 仍由 `hum2midi.service` 管理。开发机反向隧道命令是：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_hum2midi_tunnel.ps1
```

隧道断开时 `/hum2midi/` 会暂时返回 502；脚本会自动重连。公网机自身不需要 Stable Audio 或 GPU 模型。

## 开发机后端

新版本使用 `native.api`，模型配置通过环境变量指向局域网 Worker：

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r native\requirements.txt
$env:NATIVE_WORKER_URL = "http://192.168.9.100:8765"
powershell -ExecutionPolicy Bypass -File scripts\start_native_backend.ps1 -Port 8000
```

本地页面：`http://localhost:8000/`；桌面 16:9 调试视图：`http://localhost:8000/?debug=16x9`。

## GPU Worker

Worker 是 Windows RTX 5060 Ti 上的常驻进程。首次部署或守护进程脚本变更后，在 GPU 工作区执行一次：

```powershell
git pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_diffsynth_worker_daemon.ps1 -Port 8765 -PollSeconds 30
```

守护进程启动 `native.worker_server:app`，每 15 或 30 秒检查 `origin/main`。发现更新后，它会等待当前任务、拉取代码、重启模型子进程和做健康检查。只改 Worker 代码时不需要手动重启模型；只有守护进程脚本自身改变时需要手动重启一次守护进程。

确认：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health | ConvertTo-Json -Depth 5
```

必须看到 `provider=DiffSynth-Music`、`control=prosody`、RTX 5060 Ti 和 `execution=official_prosody_quick_start`。Worker 只接受官方 Prosody 条件，不静默切换其它模型。

## 公网验收

```bash
curl https://kr.sunyongfei.cn/hum2midi/api/health
systemctl is-active hum2midi nginx
```

页面上传真实哼唱后，Funk 或 Lo-fi 任一完成就先展示，另一个继续生成。后端日志和任务清单位于开发机 `runtime/native_jobs/`；Worker 阶段日志位于 GPU 工作区 `remote/logs/`。
