"""Post-production layers applied after the montage, in order.

The MVP ships with no layer: no text, no voice-over, no face. A future
"presenter" layer (your face in a corner + your voice) only needs to implement
`apply` and be appended to the list passed to `apply_layers`, for example:

    class PresenterLayer(Layer):
        name = "presenter"
        def __init__(self, face_video: Path, voice_audio: Path): ...
        def apply(self, video: Path, out: Path) -> Path:
            # ffmpeg overlay=W-w-40:H-h-240 + amix of the voice track
            ...
"""
from __future__ import annotations

import shutil
from pathlib import Path


class Layer:
    name = "layer"

    def apply(self, video: Path, out: Path) -> Path:
        raise NotImplementedError


def apply_layers(video: Path, layers: list[Layer], final: Path) -> Path:
    current = video
    for k, layer in enumerate(layers):
        current = layer.apply(current, video.with_name(f"layer_{k}_{layer.name}.mp4"))
    if current != final:
        shutil.move(str(current), final)
    return final
