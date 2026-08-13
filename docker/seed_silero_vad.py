#!/usr/bin/env python3
"""Copy Silero VAD ONNX from pip silero_vad into models/ (Docker build)."""
import shutil
from importlib.resources import as_file, files
from pathlib import Path

dest = Path("models/snakers4_silero-vad/src/silero_vad/data/silero_vad.onnx")
ref = files("silero_vad.data").joinpath("silero_vad.onnx")
if not ref.is_file():
    ref = files("silero_vad").joinpath("data", "silero_vad.onnx")
dest.parent.mkdir(parents=True, exist_ok=True)
with as_file(ref) as src:
    shutil.copy2(src, dest)
print(f"Silero VAD model ready: {dest} ({dest.stat().st_size} bytes)")
