#!/usr/bin/env bash
set -euo pipefail

# 检查必要的环境变量
: "${UPROJECT:?UPROJECT is required, e.g. /workspace/CameraControl.uproject}"
: "${PY_SCRIPT:?PY_SCRIPT is required, e.g. /workspace/Content/Python/movie_render.py}"

echo "=== Unreal Engine Headless Render ==="
echo "Project Path: $UPROJECT"
echo "Python Script: $PY_SCRIPT"

UE_CMD="/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd"

if [ ! -f "$UE_CMD" ]; then
    echo "Error: UnrealEditor-Cmd not found at $UE_CMD!"
    exit 1
fi

"$UE_CMD" "$UPROJECT" \
  -unattended -NoSplash -NoLoadingScreen -NoSound -NoP4 \
  -RenderOffscreen \
  -stdout -FullStdOutLogOutput \
  -ExecutePythonScript="$PY_SCRIPT" \
  -log