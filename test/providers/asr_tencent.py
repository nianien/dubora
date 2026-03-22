"""腾讯云录音文件识别 provider。

CreateRecTask 提交 + DescribeTaskStatus 轮询。
需要环境变量：TENCENT_SECRET_ID, TENCENT_SECRET_KEY
pip install tencentcloud-sdk-python-asr
"""

import os
import sys
import time
from typing import Optional

from .base import ASRProvider


class TencentASRProvider(ASRProvider):
    name = "tencent"
    input_type = "url"

    def __init__(self, secret_id: Optional[str] = None, secret_key: Optional[str] = None, region: str = "ap-shanghai"):
        from tencentcloud.common.credential import Credential
        from tencentcloud.asr.v20190614 import asr_client

        sid = secret_id or os.getenv("TENCENT_SECRET_ID")
        skey = secret_key or os.getenv("TENCENT_SECRET_KEY")
        if not sid or not skey:
            raise RuntimeError("需要 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY")

        self.client = asr_client.AsrClient(Credential(sid, skey), region)

    def transcribe(self, audio_input: str, **kwargs) -> dict:
        from tencentcloud.asr.v20190614 import models as asr_models

        req = asr_models.CreateRecTaskRequest()
        req.EngineModelType = "16k_zh"
        req.ChannelNum = 1
        req.ResTextFormat = 3
        req.SourceType = 0
        req.Url = audio_input
        req.SpeakerDiarization = 1
        req.SpeakerNumber = 0
        req.EmotionRecognition = 2

        print("[INFO] 腾讯云 ASR 提交任务...", file=sys.stderr)
        resp = self.client.CreateRecTask(req)
        task_id = resp.Data.TaskId
        print(f"[INFO] TaskId={task_id}", file=sys.stderr)

        while True:
            query = asr_models.DescribeTaskStatusRequest()
            query.TaskId = task_id
            data = self.client.DescribeTaskStatus(query).Data

            if data.StatusStr == "success":
                print(f"[INFO] 完成 ({data.AudioDuration:.1f}s)", file=sys.stderr)
                break
            elif data.StatusStr == "failed":
                raise RuntimeError(f"腾讯云 ASR 失败: {data.ErrorMsg}")
            print(f"[INFO] 等待中... ({data.StatusStr})", file=sys.stderr)
            time.sleep(3)

        return {
            "TaskId": data.TaskId,
            "Status": data.Status,
            "AudioDuration": data.AudioDuration,
            "Result": data.Result,
            "ResultDetail": [
                {
                    "FinalSentence": d.FinalSentence,
                    "StartMs": d.StartMs,
                    "EndMs": d.EndMs,
                    "SpeakerId": d.SpeakerId,
                    "SpeechSpeed": d.SpeechSpeed,
                    "EmotionType": d.EmotionType,
                    "Words": [
                        {"Word": w.Word, "OffsetStartMs": w.OffsetStartMs, "OffsetEndMs": w.OffsetEndMs}
                        for w in (d.Words or [])
                    ],
                }
                for d in (data.ResultDetail or [])
            ],
        }
