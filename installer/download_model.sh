#!/usr/bin/env bash
#
# download_model.sh — fetch an NVFP4 LLM checkpoint for the SupCom LLM AI bot, via aria2c.
#
# OPTIONAL / ALTERNATIVE engine path. The active default is KoboldCpp on Windows
# (see installer/download_koboldcpp.ps1). Use this only for llm.engine = "vllm".
#
# Default model: nvidia/Qwen3-14B-NVFP4 (~8 GB, official NVIDIA NVFP4 / modelopt).
# vLLM — the engine that consumes NVFP4 — is Linux-only, so RUN THIS INSIDE WSL2.
# The Windows bridge never touches these files; it talks to vLLM over :8000/v1.
#
# Downloads run through aria2c (16 parallel segments, resumable). Files are
# resolved live from the HF API so swapping MODEL_REPO keeps working.
#
# Usage (from a WSL2 shell):
#   bash installer/download_model.sh
#   MODEL_REPO=nvidia/Qwen3-14B-NVFP4 LOCAL_DIR="$HOME/models/qwen3-14b-nvfp4" \
#       bash installer/download_model.sh
#
# If you see "$'\r': command not found", fix line endings once:
#   sed -i 's/\r$//' installer/*.sh
set -euo pipefail

MODEL_REPO="${MODEL_REPO:-nvidia/Qwen3-14B-NVFP4}"
LOCAL_DIR="${LOCAL_DIR:-$HOME/models/$(basename "$MODEL_REPO")}"

echo "==> Model:  $MODEL_REPO"
echo "==> Target: $LOCAL_DIR"

# 1. aria2c + python3 are required.
if ! command -v aria2c >/dev/null 2>&1; then
  echo "!! aria2c not found. Install it:  sudo apt-get install -y aria2" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "!! python3 not found (needed to read the HF file list)." >&2
  exit 1
fi

# 2. Optional auth — repo is Apache-2.0 (ungated), but HF_TOKEN avoids rate limits.
HDR=()
if [[ -n "${HF_TOKEN:-}" ]]; then
  HDR=(--header="Authorization: Bearer $HF_TOKEN")
fi

# 3. Resolve the repo's file list from the HF API.
API="https://huggingface.co/api/models/$MODEL_REPO"
mapfile -t FILES < <(python3 - "$API" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1]) as r:
    meta = json.load(r)
for s in meta.get("siblings", []):
    print(s["rfilename"])
PY
)
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "!! No files found in $MODEL_REPO." >&2
  exit 1
fi
echo "==> ${#FILES[@]} files to fetch."

# 4. Download each file with aria2c (16-way, resumable).
mkdir -p "$LOCAL_DIR"
for f in "${FILES[@]}"; do
  url="https://huggingface.co/$MODEL_REPO/resolve/main/$f?download=true"
  dir="$LOCAL_DIR/$(dirname "$f")"
  mkdir -p "$dir"
  echo "==> $f"
  aria2c -x16 -s16 -k1M -c --file-allocation=none --console-log-level=warn \
    --summary-interval=10 "${HDR[@]}" -d "$dir" -o "$(basename "$f")" "$url"
done

if [[ ! -f "$LOCAL_DIR/config.json" ]]; then
  echo "!! WARNING: $LOCAL_DIR/config.json not found — download may be incomplete." >&2
  exit 1
fi

echo
echo "==> Done. Size on disk:"
du -sh "$LOCAL_DIR"
echo
echo "Next: bash installer/serve_vllm.sh \"$LOCAL_DIR\"   (then set llm.engine=\"vllm\")"
