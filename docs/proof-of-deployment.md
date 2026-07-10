# Proof of Alibaba Cloud Deployment

Single page for judges. Everything below is independently verifiable while
the app is live.

## 1. Qwen Cloud API usage (code)

- **Code file:** [`backend/qwen_client.py`](../backend/qwen_client.py) — every
  model call in ENGRAM goes through this one module.
- **Base URL in code:** `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
  (Qwen Cloud / Alibaba Cloud Model Studio, international).
- **Models:** `qwen3.7-plus` (reasoning) · `qwen3.6-flash` (memory
  extraction / arbitration / consolidation) · `text-embedding-v4`
  (256-d recall vectors).
- The exact file is also served read-only **from the production ECS
  itself**: https://engram.hackthon.site/qwen_client.py

## 2. Alibaba Cloud runtime (ECS)

| Item | Value |
|---|---|
| Instance ID | `i-2zefhmpp3htrijv7plwr` |
| Region / zone | `cn-beijing` / `cn-beijing-c` |
| Public IP | `47.93.234.51` |
| Stack | nginx (public HTTPS ingress) → systemd service `engram` → Python-stdlib backend on `127.0.0.1` → SQLite (WAL) |
| Live app | https://engram.hackthon.site (global mirror: https://engram.axiqo.xyz) |

The application process deliberately binds only the loopback interface;
nginx is the sole public entry point (TLS, rate limiting, static frontend).

Instance identity read from the Alibaba Cloud **ECS metadata service**
(only reachable from inside an ECS instance):

```console
$ curl http://100.100.100.200/latest/meta-data/instance-id
i-2zefhmpp3htrijv7plwr
$ curl http://100.100.100.200/latest/meta-data/region-id
cn-beijing
$ curl http://100.100.100.200/latest/meta-data/eipv4
47.93.234.51
```

Service state on the instance (captured 2026-07-05 CST):

```console
$ systemctl status engram
● engram.service - ENGRAM neural memory engine (Qwen Cloud powered)
   Loaded: loaded (/etc/systemd/system/engram.service; enabled)
   Active: active (running) since Sat 2026-07-04 04:18:00 CST; 1 day 7h ago
 Main PID: 4419 (python3)
   Memory: 14.6M (limit: 180.0M)
```

Live health endpoint (public — run this yourself):

```console
$ curl https://engram.hackthon.site/api/health
{"ok": true, "engine": "engram/1.0", "chat_model": "qwen3.7-plus",
 "fast_model": "qwen3.6-flash", "embed_model": "text-embedding-v4",
 "provider": "Qwen Cloud (Alibaba Cloud Model Studio)"}
```

## 3. Visual evidence

- **Architecture + deployment proof board:**
  ![proof](architecture.png)
- **Deployment proof recording (separate from the demo):**
  https://youtu.be/DDso1eEqKTo — live health endpoint, the integration
  source served from the ECS, and Qwen Cloud free-tier consumption for this
  account (UID `5503583088394299`).
- Additional console screenshots (Qwen Cloud usage dashboard, ECS Workbench)
  are attached in the Devpost submission's *Proof of Alibaba Cloud
  Deployment* field.

## 4. Reproduce in one minute

```bash
curl https://engram.hackthon.site/api/health       # provider string
curl https://engram.hackthon.site/qwen_client.py   # the code, from the ECS
open https://engram.hackthon.site                  # full app, live
```
