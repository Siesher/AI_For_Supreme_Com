#!/usr/bin/env bash
#
# serve_vllm.sh — serve an NVFP4 checkpoint with vLLM on NVIDIA Blackwell (sm_120)
# for the SupCom LLM AI bot. Exposes an OpenAI-compatible /v1 endpoint that the
# bot's OpenAIClient (llm.engine = "vllm") talks to.
#
# WHERE TO RUN: inside WSL2 (Ubuntu). vLLM is Linux-only.
#
# PREREQUISITES (one-time, in WSL2):
#   - NVIDIA driver on Windows with WSL2 GPU support (Blackwell / CUDA 12.8+)
#   - vLLM >= 0.19.0  (NVFP4 sm_120 fast path):   uv pip install "vllm>=0.19.0"
#   - The model downloaded via installer/download_model.sh
#
# VRAM NOTE: vLLM (WSL2) and SupCom (Windows) share the SAME physical 16 GB card.
# --gpu-memory-utilization 0.80 caps vLLM at ~12.8 GB (8 GB weights + KV + overhead)
# and leaves ~3 GB for the game. Lower it if the game stutters.
#
# TOOL CALLING: --enable-auto-tool-choice + --tool-call-parser hermes is what makes
# vLLM emit structured `tool_calls` in the OpenAI response. WITHOUT these flags the
# bot only gets plain text and falls back to the XML parser — keep them on.
#
# Usage:
#   bash installer/serve_vllm.sh [MODEL_DIR_OR_REPO]
set -euo pipefail

MODEL="${1:-$HOME/models/Qwen3-14B-NVFP4}"
SERVED_NAME="${SERVED_NAME:-qwen3-14b-nvfp4}"
PORT="${PORT:-8000}"
GPU_UTIL="${GPU_UTIL:-0.80}"
MAX_LEN="${MAX_LEN:-8192}"

echo "==> Serving $MODEL as '$SERVED_NAME' on :$PORT (gpu-util=$GPU_UTIL, ctx=$MAX_LEN)"

# --quantization modelopt: NVIDIA modelopt NVFP4 checkpoints. If a newer vLLM
#   rejects it, try '--quantization modelopt_fp4' or drop the flag (auto-detect
#   from hf_quant_config.json).
# --max-num-seqs 1: the bot runs a single decision stream; keeps KV cache tiny.
exec vllm serve "$MODEL" \
  --served-model-name "$SERVED_NAME" \
  --quantization modelopt \
  --port "$PORT" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --max-model-len "$MAX_LEN" \
  --max-num-seqs 1 \
  --kv-cache-dtype fp8 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
