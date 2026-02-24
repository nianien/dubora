"""
Doubao ASR 数据解析器

职责：
- raw JSON → 结构化数据（返回通用的 Utterance/Word）
- speaker 字段识别
- 时间统一为 ms

禁止：
- ❌ 合并句子
- ❌ speaker 策略
- ❌ 任何业务规则

注意：返回的 Utterance/Word 使用通用的类型定义（dubora.schema），
不绑定到 doubao 特定的类型，以便支持多 provider。
"""
from typing import Any, Dict, List, Optional

# 使用通用的类型定义（不绑定到 doubao）
from dubora.schema import Utterance, Word
from dubora.utils.text import normalize_text


def parse_words(word_list: List[Dict[str, Any]], default_speaker: str = "") -> List[Word]:
    """
    解析 words 列表。
    
    支持的格式：
      words[i] = {
        "start_time": 5440,
        "end_time": 6000,
        "text": "你好",
        "additions": {"speaker": "1"}  # 可选
      }
    """
    words: List[Word] = []
    for w in word_list:
        st = int(w.get("start_time", 0))
        et = int(w.get("end_time", st))
        txt = str(w.get("text", "")).strip()
        if not txt:
            continue
        
        # 尝试从 word 级别获取 speaker，否则使用默认值
        w_additions = w.get("additions") or {}
        w_spk = str(w_additions.get("speaker", default_speaker))
        
        words.append(Word(start_ms=st, end_ms=et, text=txt, speaker=w_spk))
    
    return words


def parse_utterances(raw: Dict[str, Any]) -> List[Utterance]:
    """
    Supports your raw format:
      raw["result"]["utterances"][i] = {
        "additions": {"speaker": "1"},
        "start_time": 5440,
        "end_time": 11840,
        "text": "...",
        "words": [
          {"start_time": 5440, "end_time": 6000, "text": "你好", "additions": {"speaker": "1"}},
          ...
        ]
      }
    """
    result = raw.get("result") or {}
    uts = result.get("utterances") or []
    out: List[Utterance] = []
    for u in uts:
        additions = u.get("additions") or {}
        spk = str(additions.get("speaker", "0"))
        st = int(u.get("start_time", 0))
        et = int(u.get("end_time", st))
        text = normalize_text(str(u.get("text", "")))
        if not text:
            continue
        
        # 解析 emotion 和 gender（如果存在）
        emotion = additions.get("emotion")  # 可能是 None
        gender = additions.get("gender")  # 可能是 None
        
        # 解析 words（如果存在）
        words: Optional[List[Word]] = None
        word_list = u.get("words")
        if word_list and isinstance(word_list, list):
            words = parse_words(word_list, default_speaker=spk)
        
        out.append(Utterance(
            speaker=spk,
            start_ms=st,
            end_ms=et,
            text=text,
            words=words,
            emotion=str(emotion) if emotion is not None else None,
            gender=str(gender) if gender is not None else None,
        ))
    # ensure time order
    out.sort(key=lambda x: (x.start_ms, x.end_ms))
    return out
