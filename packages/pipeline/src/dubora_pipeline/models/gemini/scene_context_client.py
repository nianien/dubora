"""
Gemini 视频/音频场景上下文分析

用 Gemini 多模态理解能力，输入音频（或视频）URL，输出一段简洁的
"业务场景描述"，供下游 ASR 调用作为 corpus.context dialog_ctx 上下文。

实测在快语速广告类音频上能把豆包 2.0 的识别准确率从 ~50% 拉到 ~92%。

输出格式：单段自然语言文本（200 字以内）
存储格式：JSON 文件 asr-context.json
"""


_PROMPT_SYSTEM = """你是一个音视频内容分析师。
任务：先听一遍给定的音频，仔细听清每个词，
然后输出一段精炼的"业务场景描述"。

这段描述会被作为 ASR (语音识别) 模型的辅助上下文，帮助它准确识别对白。

输出要求（严格）：

1. 单段自然语言，**不超过 300 字**，不要 Markdown / 前言 / 解释

2. 必须包含：
   - 视频类型（短剧 / 广告 / Vlog / ASMR / 带货 / 教学 / 纪录片 等）
   - 对白格式特点（清单式 / 对话式 / 念稿式 / 旁白 / 单人独白 等）
   - **关键名词清单**：列出音频中出现的所有
     专有名词、人名、地名、品牌名、产品名、菜名/食材、术语
   - **核心词强调**：对于可能被同音字误识别的关键词，
     用"准确说法是 X"或"应识别为 X"句式强调（**仅列出正确形式，不要列错误候选！**）

3. 名词用顿号(、)分隔，便于 ASR 检索

示例：

GOOD（仅正确形式）：
"这是一段12星座+零食创意 ASMR 广告，气音念稿式独白，每段格式：星座名+对应零食。
关键名词：白羊座、金牛座、双子座、巨蟹座、狮子座、处女座、天秤座、天蝎座、射手座、
摩羯座、水瓶座、双鱼座、棉花糖、蛋卷、海苔、香肠、奶糖、巧克力、辣条、果冻、
曲奇饼干、薯片、棒棒糖、干脆面。
对食物名称要准确：海苔(海洋食材)、蛋卷(脆饼类)、奶糖(糖果类)、干脆面(方便面零食)。"

BAD（不要这样写）：
"海苔(不是海带)、蛋卷(不是的卷)、干脆面(不是催眠)..."
原因：列出错误候选会让 ASR 误以为这些是合理选项，反而污染识别结果。
"""

_PROMPT_USER = "请分析这段音频，输出业务场景描述。"


def generate_scene_context(
    audio_url: str,
    *,
    api_key: str,
    model_name: str = "gemini-3.5-flash",
    mime_type: str = "audio/wav",
) -> str:
    """让 Gemini 听一遍音频，输出业务场景描述。

    Args:
        audio_url: 音频文件签名 URL（GCS 或可公开访问的 URL）
        api_key: Gemini API key
        model_name: Gemini 模型名称
        mime_type: 音频 MIME 类型（audio/wav / audio/mp3 等）

    Returns:
        业务场景描述文本（单段，<=200 字）。生成失败抛 RuntimeError。

    Raises:
        RuntimeError: Gemini 调用失败或返回空
    """
    from google import genai
    from google.genai import types

    if not api_key:
        raise RuntimeError("Gemini scene context 需要 api_key 参数")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=[
            types.Content(parts=[
                types.Part.from_uri(file_uri=audio_url, mime_type=mime_type),
                types.Part.from_text(text=_PROMPT_USER),
            ])
        ],
        config=types.GenerateContentConfig(
            system_instruction=_PROMPT_SYSTEM,
            # 不要 response_mime_type=json — 这里要纯文本
        ),
    )

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError(
            "Gemini scene context returned empty. "
            "Likely transient API issue (safety filter / quota / URL fetch failure). Retry recommended."
        )
    return text
