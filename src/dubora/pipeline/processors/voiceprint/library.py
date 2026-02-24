"""
剧级声纹库管理

存储路径：videos/{series}/dub/voiceprint/library.json

支持：
- cosine 匹配（match）
- 注册新角色（register）
- EMA 滚动更新 embedding（update）
- 持久化（save / load）
"""
import json
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Optional

from dubora.utils.logger import info, warning


class VoiceprintLibrary:
    """剧级声纹库。"""

    def __init__(self, library_path: str):
        """
        Args:
            library_path: library.json 的路径
        """
        self.library_path = Path(library_path)
        self.characters: Dict[str, Dict[str, Any]] = {}
        self._next_id = 1
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """从 JSON 文件加载声纹库。"""
        if not self.library_path.exists():
            self.characters = {}
            self._next_id = 1
            return

        with open(self.library_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.characters = {}
        for char_id, char_data in data.get("characters", {}).items():
            self.characters[char_id] = {
                "name": char_data.get("name"),
                "gender": char_data.get("gender"),
                "embedding": np.array(char_data["embedding"], dtype=np.float32),
                "episode_count": char_data.get("episode_count", 0),
                "total_duration_s": char_data.get("total_duration_s", 0.0),
                "reference_clip": char_data.get("reference_clip"),
            }

        # 计算下一个 ID
        if self.characters:
            max_num = max(
                int(cid.split("_")[1]) for cid in self.characters if "_" in cid
            )
            self._next_id = max_num + 1
        else:
            self._next_id = 1

        info(f"Loaded voiceprint library: {len(self.characters)} characters")

    def save(self) -> None:
        """保存声纹库到 JSON。"""
        self.library_path.parent.mkdir(parents=True, exist_ok=True)

        data = {"characters": {}}
        for char_id, char_data in self.characters.items():
            emb = char_data["embedding"]
            data["characters"][char_id] = {
                "name": char_data.get("name"),
                "gender": char_data.get("gender"),
                "embedding": emb.tolist() if isinstance(emb, np.ndarray) else emb,
                "episode_count": char_data.get("episode_count", 0),
                "total_duration_s": char_data.get("total_duration_s", 0.0),
                "reference_clip": char_data.get("reference_clip"),
            }

        with open(self.library_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        info(f"Saved voiceprint library: {len(self.characters)} characters -> {self.library_path}")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def match(
        self,
        embedding: np.ndarray,
        threshold: float = 0.65,
    ) -> Optional[str]:
        """
        在声纹库中查找最匹配的角色。

        Args:
            embedding: 192 维 embedding（已 L2 归一化）
            threshold: cosine similarity 阈值

        Returns:
            匹配的 char_id，如果无匹配返回 None
        """
        if not self.characters:
            return None

        best_id = None
        best_score = -1.0

        for char_id, char_data in self.characters.items():
            lib_emb = char_data["embedding"]
            if isinstance(lib_emb, list):
                lib_emb = np.array(lib_emb, dtype=np.float32)
            score = float(np.dot(embedding, lib_emb))
            if score > best_score:
                best_score = score
                best_id = char_id

        if best_score >= threshold:
            info(f"Voiceprint match: {best_id} (cosine={best_score:.3f})")
            return best_id

        info(f"No voiceprint match (best cosine={best_score:.3f} < threshold={threshold})")
        return None

    def register(
        self,
        embedding: np.ndarray,
        gender: Optional[str] = None,
        duration_s: float = 0.0,
    ) -> str:
        """
        注册新角色到声纹库。

        Args:
            embedding: 192 维 embedding
            gender: 性别
            duration_s: 该 speaker 的累计音频时长

        Returns:
            新分配的 char_id
        """
        char_id = f"char_{self._next_id:03d}"
        self._next_id += 1

        self.characters[char_id] = {
            "name": None,
            "gender": gender,
            "embedding": embedding.copy(),
            "episode_count": 1,
            "total_duration_s": duration_s,
            "reference_clip": None,
        }

        info(f"Registered new character: {char_id} (gender={gender}, duration={duration_s:.1f}s)")
        return char_id

    def update(
        self,
        char_id: str,
        new_embedding: np.ndarray,
        duration_s: float = 0.0,
        alpha: float = 0.3,
    ) -> None:
        """
        EMA 更新角色的 embedding。

        new = alpha * new_embedding + (1 - alpha) * old_embedding

        Args:
            char_id: 角色 ID
            new_embedding: 新 embedding
            duration_s: 本集的音频时长
            alpha: EMA 权重（新集权重）
        """
        if char_id not in self.characters:
            warning(f"Cannot update unknown character: {char_id}")
            return

        char = self.characters[char_id]
        old_emb = char["embedding"]
        if isinstance(old_emb, list):
            old_emb = np.array(old_emb, dtype=np.float32)

        # EMA 更新
        updated = alpha * new_embedding + (1.0 - alpha) * old_emb
        # 重新归一化
        norm = np.linalg.norm(updated)
        if norm > 0:
            updated = updated / norm

        char["embedding"] = updated
        char["episode_count"] = char.get("episode_count", 0) + 1
        char["total_duration_s"] = char.get("total_duration_s", 0.0) + duration_s

        info(f"Updated character {char_id}: episode_count={char['episode_count']}, total_duration={char['total_duration_s']:.1f}s")

    def set_reference_clip(self, char_id: str, clip_relpath: str) -> None:
        """设置角色的参考音频路径。"""
        if char_id in self.characters:
            self.characters[char_id]["reference_clip"] = clip_relpath

    def get_all_char_ids(self) -> List[str]:
        """返回所有角色 ID。"""
        return list(self.characters.keys())
