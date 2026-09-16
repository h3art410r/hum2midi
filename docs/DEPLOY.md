# Demo 生产机部署记录

目标机：`ubuntu@kr.sunyongfei.cn`（Ubuntu 24.04，Python 3.12）。

## 当前服务

- 应用目录：`/home/ubuntu/hum2midi`
- systemd：`hum2midi.service`
- Uvicorn：`127.0.0.1:8000`（由 Nginx 反代）
- Nginx：`80 → 443`，HTTP 自动跳转 HTTPS
- 访问地址：<https://kr.sunyongfei.cn>
- 音频组件：系统 FFmpeg、FluidSynth 2.3.4、`fluid-soundfont-gm`
- API 密钥只存在服务器 `.env`（权限 `600`），没有进入 Git 或日志

## 可选：腾讯云人声转录 A/B

腾讯智能音乐的人声转录任务要求服务端提交一个可访问的 HTTPS 音频 URL。启用前在服务器 `.env` 增加以下运行时配置（不要提交到仓库）：

```bash
TRANSCRIPTION_ENGINE=tencent
TENCENT_SECRET_ID=...
TENCENT_SECRET_KEY=...
H2M_PUBLIC_BASE_URL=https://kr.sunyongfei.cn
```

应用会把规范化后的临时 WAV 暴露在随机 job id 路由，任务完成后再由 provider 下载 MIDI。若只想保留当前基线，使用 `TRANSCRIPTION_ENGINE=dsp`；`auto` 会在检测到腾讯凭据时优先选择腾讯，否则使用 DSP。云端任务失败不会静默切换引擎。

## 验收

```bash
curl https://kr.sunyongfei.cn/api/health
systemctl is-active hum2midi nginx
```

健康检查应报告 `renderer: fluidsynth-soundfont`。真实哼唱复测 job：`fe96c26e463d472c857ddf76c69a8398`，canonical MIDI 14 音，Funk/Lofi 风格 MIDI 137/46 音符，两个音频接口均返回 200。

## 更新流程

在开发机完成测试后，只同步被 Git 跟踪的代码和文档，不同步 `.env`、`.venv`、`data/`：

```bash
sudo systemctl restart hum2midi
sudo journalctl -u hum2midi -f
```

证书由 Certbot 自动续期；证书到期时间和续期任务可用 `sudo certbot certificates`、`systemctl list-timers | grep certbot` 检查。
