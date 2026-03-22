"""腾讯云录音文件识别。

CreateRecTask 提交 + DescribeTaskStatus 轮询。
需要环境变量：TENCENT_SECRET_ID, TENCENT_SECRET_KEY
pip install tencentcloud-sdk-python-asr
"""

import os
import time

from dubora_core.utils.logger import info


def transcribe_tencent(audio_url: str) -> dict:
    """提交腾讯云 ASR 任务并轮询结果。

    Args:
        audio_url: 音频文件的公网 URL

    Returns:
        原始响应 dict，含 ResultDetail（逗号级分段 + 说话人 + 情绪）
    """
    from tencentcloud.common.credential import Credential
    from tencentcloud.asr.v20190614 import asr_client, models as asr_models

    sid = os.getenv("TENCENT_SECRET_ID")
    skey = os.getenv("TENCENT_SECRET_KEY")
    if not sid or not skey:
        raise RuntimeError("需要 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY")

    client = asr_client.AsrClient(Credential(sid, skey), "ap-shanghai")

    req = asr_models.CreateRecTaskRequest()
    req.EngineModelType = "16k_zh"
    req.ChannelNum = 1
    req.ResTextFormat = 3
    req.SourceType = 0
    req.Url = audio_url
    req.SpeakerDiarization = 1
    req.SpeakerNumber = 0
    req.EmotionRecognition = 2

    info("腾讯云 ASR 提交任务...")
    resp = client.CreateRecTask(req)
    task_id = resp.Data.TaskId
    info(f"腾讯云 ASR TaskId={task_id}")

    while True:
        query = asr_models.DescribeTaskStatusRequest()
        query.TaskId = task_id
        data = client.DescribeTaskStatus(query).Data

        if data.StatusStr == "success":
            info(f"腾讯云 ASR 完成 ({data.AudioDuration:.1f}s)")
            break
        elif data.StatusStr == "failed":
            raise RuntimeError(f"腾讯云 ASR 失败: {data.ErrorMsg}")
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
