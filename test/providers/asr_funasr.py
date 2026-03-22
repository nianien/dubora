"""FunASR 本地 ASR provider。"""

import sys
from pathlib import Path

from .base import ASRProvider


class FunASRProvider(ASRProvider):
    name = "funasr"
    input_type = "file"

    def __init__(self, model_name: str = "paraformer-zh", device: str = "cpu"):
        from funasr import AutoModel

        self.model = AutoModel(
            model=model_name,
            vad_model="fsmn-vad",
            punc_model="ct-punc-c",
            spk_model="cam++",
            device=device,
        )

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        path = Path(audio_input)
        if not path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_input}")

        print(f"[INFO] FunASR...", file=sys.stderr)
        res = self.model.generate(input=str(path), batch_size_s=300)
        return res[0] if res else {}
