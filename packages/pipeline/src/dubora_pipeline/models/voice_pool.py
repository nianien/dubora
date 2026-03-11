"""
Voice Pool: 声线池管理
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Any


# 默认声线池（8 条 en-US 声线）
DEFAULT_VOICE_POOL = {
    "language": "en-US",
    "voices": {
        "adult_male_1": {
            "voice_id": "en-US-GuyNeural",
            "name": "Guy",
            "gender": "male",
            "role": "male_lead",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_male_2": {
            "voice_id": "en-US-DavisNeural",
            "name": "Davis",
            "gender": "male",
            "role": "male_supporting",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_male_3": {
            "voice_id": "en-US-JasonNeural",
            "name": "Jason",
            "gender": "male",
            "role": "male_young",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_male_4": {
            "voice_id": "en-US-TonyNeural",
            "name": "Tony",
            "gender": "male",
            "role": "male_generic",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_female_1": {
            "voice_id": "en-US-JennyNeural",
            "name": "Jenny",
            "gender": "female",
            "role": "female_lead",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_female_2": {
            "voice_id": "en-US-AriaNeural",
            "name": "Aria",
            "gender": "female",
            "role": "female_mature",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_female_3": {
            "voice_id": "en-US-AmberNeural",
            "name": "Amber",
            "gender": "female",
            "role": "female_young",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
        "adult_female_4": {
            "voice_id": "en-US-AnaNeural",
            "name": "Ana",
            "gender": "female",
            "role": "female_generic",
            "prosody": {"rate": 1.0, "pitch": 0, "volume": 0},
        },
    },
}


class VoicePool:
    """声线池管理器。"""
    
    def __init__(self, pool_path: Optional[str] = None):
        """
        初始化 VoicePool。
        
        Args:
            pool_path: 声线池 JSON 文件路径（None = 使用默认）
        """
        if pool_path and Path(pool_path).exists():
            with open(pool_path, "r", encoding="utf-8") as f:
                self.pool_data = json.load(f)
        else:
            self.pool_data = DEFAULT_VOICE_POOL
    
    def get_voice(self, pool_key: str) -> Optional[Dict[str, Any]]:
        """
        根据 pool_key 获取 voice 配置。
        
        Args:
            pool_key: 声线键（如 "adult_male_1"）
        
        Returns:
            voice 配置字典，如果不存在则返回 None
        """
        return self.pool_data.get("voices", {}).get(pool_key)
    
    def get_voice_by_id(self, voice_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 id 字段获取 voice 配置（支持数组格式的 voice_pool.json）。

        Args:
            voice_id: voice 的 id（如 "v_m_deep_01"）

        Returns:
            voice 配置字典，如果不存在则返回 None
        """
        voices = self.pool_data.get("voices", [])
        if isinstance(voices, list):
            for v in voices:
                if v.get("id") == voice_id:
                    return v
        return None

    def get_all_voices(self) -> List[Dict[str, Any]]:
        """
        获取所有 voices 列表。

        Returns:
            voices 列表（每个包含 voice_id, name, gender 等）
        """
        voices = self.pool_data.get("voices", {})
        return list(voices.values())
