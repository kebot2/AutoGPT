# ⚠️ EXPERIMENTAL — AutoGPT Local PC Executor

> **DANGER: This is untested, experimental, pre-alpha software.**  
> Do not run on any machine you care about. Do not use in production.  
> Running this gives the AutoGPT platform the ability to execute arbitrary commands on your computer.  
> You have been warned.

---

## What Is This?

A local shim daemon that lets the **AutoGPT hosted platform** use your actual machine as
its code execution backend — instead of an E2B cloud sandbox.

When the shim is running and connected, AutoGPT can:
- Read and write files on your filesystem
- Execute shell commands
- (Optionally) take screenshots and control your mouse/keyboard via Claude's computer use API
- (Optionally) access local hardware (serial ports, USB devices, GPIO)
- (Optionally) route inference to a local LLM (Ollama, llama.cpp)

## Why Would You Want This?

- Access files that can't be uploaded to the cloud
- Use licensed software installed on your machine
- Control physical hardware (3D printers, Arduinos, lab instruments)
- Run tasks that need your local environment (VPN, internal network, specific OS setup)
- Privacy: keep sensitive data on-device while still using AutoGPT's orchestration

## Current Status

| Component | Status |
|-----------|--------|
| Spec / Protocol | 🟡 Draft |
| Shim daemon (Python) | 🔴 Skeleton only |
| Platform hooks | 🔴 Not implemented |
| OAuth integration | 🔴 Spec only |
| Computer use | 🔴 Spec only |
| Hardware access | 🔴 Spec only |

## Quick Start (Future — Not Working Yet)

```bash
pip install autogpt-local-executor
autogpt-shim auth          # Opens browser → AutoGPT OAuth flow
autogpt-shim start         # Starts daemon, connects to platform
```

## Architecture

See [`docs/VISION.md`](docs/VISION.md) for the full dream.  
See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the WebSocket message protocol.  
See [`docs/PLATFORM_HOOKS.md`](docs/PLATFORM_HOOKS.md) for where this plugs into AutoGPT.  
See [`docs/OAUTH_FLOW.md`](docs/OAUTH_FLOW.md) for the auth design.  
See [`docs/SECURITY.md`](docs/SECURITY.md) for the threat model and mitigations.

## Contributing

This lives in `experimental/local-pc-executor/`. PRs welcome, but understand this is
exploratory — the interface will break repeatedly.

Open issues with `[local-executor]` prefix.
