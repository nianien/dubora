"""
Manifest IO + registry + 状态管理
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dubora.pipeline.core.types import Artifact, ErrorInfo, Status
from dubora.pipeline.core.atomic import atomic_write, atomic_copy
from dubora.pipeline.core.fingerprints import hash_file


class Manifest:
    """Manifest 管理器。"""
    
    SCHEMA_VERSION = "1.0"
    
    def __init__(self, manifest_path: Path):
        self.manifest_path = manifest_path
        self.data: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "job": {},
            "artifacts": {},
            "phases": {},
        }
        self._load()
    
    def _load(self) -> None:
        """从文件加载 manifest。"""
        if self.manifest_path.exists():
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            # 初始化新 manifest
            self.data = {
                "schema_version": self.SCHEMA_VERSION,
                "job": {},
                "artifacts": {},
                "phases": {},
            }
    
    def save(self) -> None:
        """保存 manifest 到文件（原子写入）。"""
        content = json.dumps(self.data, indent=2, ensure_ascii=False)
        atomic_write(content, self.manifest_path)
    
    def set_job(self, job_id: str, workspace: str) -> None:
        """设置 job 信息。"""
        self.data["job"] = {
            "job_id": job_id,
            "workspace": workspace,
        }
    
    def get_artifact(self, key: str, required_by: Optional[str] = None) -> Artifact:
        """
        获取 artifact（找不到就报错并提示 requires 缺失）。
        
        Args:
            key: artifact key
            required_by: 请求该 artifact 的 phase 名称（用于错误提示）
        
        Returns:
            Artifact 对象
        
        Raises:
            ValueError: 如果 artifact 不存在
        """
        artifact_data = self.data["artifacts"].get(key)
        if artifact_data is None:
            error_msg = f"Required artifact '{key}' not found in manifest"
            if required_by:
                error_msg += f" (required by phase '{required_by}')"
            error_msg += f". Available artifacts: {list(self.data['artifacts'].keys())}"
            raise ValueError(error_msg)
        
        # 兼容旧版本：旧 manifest 使用 "path" 字段，新版本使用 "relpath"
        relpath = artifact_data.get("relpath")
        if relpath is None and "path" in artifact_data:
            relpath = artifact_data["path"]
            # 尝试就地升级内存中的结构，后续 save() 会写回新的字段名
            artifact_data["relpath"] = relpath
            artifact_data.pop("path", None)

        return Artifact(
            key=artifact_data["key"],
            relpath=relpath,
            kind=artifact_data["kind"],
            fingerprint=artifact_data["fingerprint"],
            meta=artifact_data.get("meta", {}),
        )
    
    def register_artifact(self, artifact: Artifact) -> None:
        """注册 artifact 到 registry。"""
        self.data["artifacts"][artifact.key] = {
            "key": artifact.key,
            "relpath": artifact.relpath,
            "kind": artifact.kind,
            "fingerprint": artifact.fingerprint,
            "meta": artifact.meta,
        }
    
    def get_phase_status(self, phase_name: str) -> Optional[Status]:
        """获取 phase 状态。"""
        phase_data = self.data["phases"].get(phase_name)
        if phase_data is None:
            return None
        return phase_data.get("status")
    
    def update_phase(
        self,
        phase_name: str,
        *,
        version: str,
        status: Status,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        attempt: int = 1,
        requires: Optional[List[str]] = None,
        provides: Optional[List[str]] = None,
        inputs_fingerprint: Optional[str] = None,
        config_fingerprint: Optional[str] = None,
        artifacts: Optional[Dict[str, Artifact]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        warnings: Optional[List[str]] = None,
        error: Optional[ErrorInfo] = None,
        skipped: Optional[bool] = None,
    ) -> None:
        """更新 phase 记录。"""
        if phase_name not in self.data["phases"]:
            self.data["phases"][phase_name] = {}
        
        phase_data = self.data["phases"][phase_name]
        phase_data["name"] = phase_name
        phase_data["version"] = version
        phase_data["status"] = status
        
        if started_at:
            phase_data["started_at"] = started_at
        if finished_at:
            phase_data["finished_at"] = finished_at
        if attempt:
            phase_data["attempt"] = attempt
        if requires is not None:
            phase_data["requires"] = requires
        if provides is not None:
            phase_data["provides"] = provides
        if inputs_fingerprint:
            phase_data["inputs_fingerprint"] = inputs_fingerprint
        if config_fingerprint:
            phase_data["config_fingerprint"] = config_fingerprint
        if artifacts:
            # 将 artifacts 转换为字典格式
            phase_data["artifacts"] = {
                key: {
                    "key": artifact.key,
                    "relpath": artifact.relpath,
                    "kind": artifact.kind,
                    "fingerprint": artifact.fingerprint,
                    "meta": artifact.meta,
                }
                for key, artifact in artifacts.items()
            }
        if metrics:
            phase_data["metrics"] = metrics
        if warnings:
            phase_data["warnings"] = warnings
        if error:
            phase_data["error"] = {
                "type": error.type,
                "message": error.message,
                "traceback": error.traceback,
            }
        if skipped is not None:
            phase_data["skipped"] = skipped
    
    def get_phase_data(self, phase_name: str) -> Optional[Dict[str, Any]]:
        """获取 phase 数据。"""
        return self.data["phases"].get(phase_name)
    
    def get_all_artifacts(self) -> Dict[str, Artifact]:
        """获取所有 artifacts。"""
        return {
            key: self.get_artifact(key)
            for key in self.data["artifacts"].keys()
        }
    
    def _resolve_artifact_path(self, key: str, workspace: Path) -> Path:
        """
        根据 artifact key 解析最终文件路径。
        
        Args:
            key: artifact key（如 "subs.subtitle_model"）
            workspace: workspace 根目录
        
        Returns:
            最终文件路径（绝对路径）
        """
        # 根据 key 的 domain 和 object 确定路径
        parts = key.split(".", 1)
        if len(parts) == 2:
            domain, obj = parts
        else:
            domain = "misc"
            obj = key
        
        # 路径映射规则（workspace-relative）
        # 按语义角色分层：
        #   source/  — SSOT，人工可编辑
        #   derive/  — 确定性派生，可重算
        #   mt/      — LLM 不稳定产物
        #   tts/     — TTS 合成产物
        #   audio/   — 声学工程
        #   render/  — 最终交付物
        path_map = {
            "demux": {
                "audio": "audio/{episode_stem}.wav",
            },
            "sep": {
                "vocals": "audio/{episode_stem}-vocals.wav",
                "accompaniment": "audio/{episode_stem}-accompaniment.wav",
            },
            "asr": {
                "asr_result": "source/asr-result.json",
                "asr_model": "source/dub.json",
            },
            "subs": {
                "subtitle_model": "source/subtitle.model.json",
                "subtitle_align": "derive/subtitle.align.json",
                "zh_srt": "render/zh.srt",
                "en_srt": "render/en.srt",
            },
            "mt": {
                "mt_input": "mt/mt_input.jsonl",
                "mt_output": "mt/mt_output.jsonl",
            },
            "tts": {
                "audio": "audio/{episode_stem}-tts.wav",
                "voice_assignment": "derive/voice-assignment.json",
                "sentence": "tts/sentence.json",
                "segments_dir": "tts/segments",
                "segments_index": "tts/segments.json",
                "report": "tts/tts_report.json",
            },
            "voiceprint": {
                "speaker_map": "derive/voiceprint/speaker_map.json",
                "reference_clips": "derive/voiceprint/refs",
            },
            "dub": {
                "dub_manifest": "source/dub.json",
            },
            "mix": {
                "audio": "audio/{episode_stem}-mix.wav",
            },
            "burn": {
                "video": "render/{episode_stem}-dubbed.mp4",
            },
        }
        
        # 获取路径模板
        if domain in path_map and obj in path_map[domain]:
            path_template = path_map[domain][obj]
        else:
            # 默认路径：domain/obj
            path_template = f"{domain}/{obj}"
        
        # 替换 episode_stem（从 workspace 名称提取）
        episode_stem = workspace.name
        path_str = path_template.format(episode_stem=episode_stem)
        
        return workspace / path_str


def now_iso() -> str:
    """返回当前时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()
