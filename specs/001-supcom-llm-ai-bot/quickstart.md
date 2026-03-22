# Quickstart Guide: SupCom:FA LLM AI Bot

## Prerequisites

- Supreme Commander: Forged Alliance installed at `C:\Program Files (x86)\Supreme Commander`
- [FAF Client](https://faforever.com) installed
- Python 3.11+ installed
- [Ollama](https://ollama.com) installed
- [Visual C++ Redistributable 2022](https://aka.ms/vs/17/release/vc_redist.x64.exe) installed (for the DLL)
- GPU: 6GB+ VRAM recommended (RTX 2080 / equivalent), or 16GB RAM for CPU fallback

---

## Step 1: Download the LLM Model

```powershell
# Pull the recommended model (~5GB download)
ollama pull qwen2.5:7b-instruct-q5_K_M
```

Verify it works:
```powershell
ollama run qwen2.5:7b-instruct-q5_K_M "Say hello in one word"
```

---

## Step 2: Install the FAF Mod

Copy the `mod/` folder to your FAF mods directory:

```powershell
# Run the installer (recommended)
.\installer\install.ps1

# Or manually:
xcopy /E /I "mod\" "$env:APPDATA\FAForever\mods\supcom-llm-ai-bot\"
```

---

## Step 3: Install the DLL Bridge

The DLL must be placed where the game can load it:

```powershell
# The installer does this automatically, or manually:
copy "bridge\build\supcom_llm_bridge.dll" "$env:APPDATA\FAForever\mods\supcom-llm-ai-bot\bin\"
```

---

## Step 4: Configure the Bot

Edit `installer/config.json`:

```json
{
  "llm": {
    "model": "qwen2.5:7b-instruct-q5_K_M",
    "base_url": "http://localhost:11434",
    "temperature": 0.3,
    "max_tokens": 256
  },
  "game": {
    "game_path": "C:\\Program Files (x86)\\Supreme Commander",
    "faction": "UEF"
  },
  "bot": {
    "difficulty": "normal",
    "playstyle": "balanced",
    "chat_enabled": true,
    "chat_language": "auto",
    "poll_interval_s": 45
  },
  "debug": {
    "overlay_enabled": true,
    "log_enabled": true
  }
}
```

---

## Step 5: Start the Python Bridge Server

Before launching the game, start the bridge server in a terminal:

```powershell
cd server
pip install -r requirements.txt
python bridge_server.py
```

You should see:
```
[LLM Bridge] Ollama connected (qwen2.5:7b-instruct-q5_K_M)
[LLM Bridge] Waiting for game connection on \\.\pipe\supcom_llm_bridge...
```

---

## Step 6: Launch a Game with the LLM AI Bot

1. Open **FAF Client**
2. Create a **Custom Game** (or **Skirmish**)
3. In AI selection, choose **"LLM AI Bot (UEF)"**
4. Set the bot's team (enemy or ally)
5. Select faction **UEF** for the bot slot
6. Start the game

In the terminal you should see:
```
[LLM Bridge] Game connected!
[LLM Bridge] First strategy query sent...
[LLM Bridge] Decision: land_push (latency: 1842ms)
```

---

## Debug Overlay

Press **Ctrl+Shift+D** in-game to toggle the bot status overlay.

The overlay shows:
- Current strategy
- Last LLM reasoning
- Economy status
- LLM connection indicator
- Time until next query

---

## Logs

Decision log is written to:
```
C:\ProgramData\FAForever\logs\llm_ai_decisions.log
```

Each line is a JSON object with the full LLM query, response, and game context.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "LLM AI Bot (UEF)" not in AI list | Verify mod is in `%APPDATA%\FAForever\mods\` and enabled in FAF mod manager |
| Bridge not connecting | Make sure `bridge_server.py` is running before starting the game |
| LLM very slow | Try a smaller model: `ollama pull qwen2.5:3b-instruct-q5_K_M` |
| Bot not building anything | Check `game.log` at `C:\ProgramData\FAForever\logs\` for Lua errors |
| Overlay not appearing | Check FAF UI mod is enabled; press Ctrl+Shift+D |

---

## Hardware Profiles

| Hardware | Recommended Model | Expected Latency |
|---|---|---|
| RTX 2080 / 8GB VRAM | qwen2.5:7b-instruct-q5_K_M | ~2-3s |
| RTX 3080 / 10GB VRAM | qwen2.5:7b-instruct-q8_0 | ~1.5-2s |
| CPU only / 32GB RAM | qwen2.5:7b-instruct-q4_K_M | ~15-30s (increase poll_interval_s to 90) |
