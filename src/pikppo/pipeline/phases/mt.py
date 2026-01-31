"""
MT Phase: 机器翻译（只编排与IO，调用 models.openai.translate）
"""
import json
from pathlib import Path
from typing import Dict

from pikppo.pipeline.core.phase import Phase
from pikppo.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from pikppo.pipeline.processors.mt import run as mt_run
from pikppo.config.settings import get_openai_api_key
from pikppo.utils.timecode import write_srt_from_segments
from pikppo.utils.logger import info


class MTPhase(Phase):
    """机器翻译 Phase。"""
    
    name = "mt"
    version = "1.0.0"
    
    def requires(self) -> list[str]:
        """需要 subs.subtitle_model（SSOT）。"""
        return ["subs.subtitle_model"]
    
    def provides(self) -> list[str]:
        """生成 translate.context, subs.en_srt。"""
        return ["translate.context", "subs.en_srt"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 MT Phase。
        
        流程：
        1. 读取 subtitle.model.json（SSOT）
        2. Stage 1: 生成翻译上下文
        3. Stage 2: 翻译 cues
        4. 更新 Subtitle Model 的 target 字段
        5. 生成 en.srt
        """
        # 获取输入（Subtitle Model SSOT）
        subtitle_model_artifact = inputs["subs.subtitle_model"]
        subtitle_model_path = Path(ctx.workspace) / subtitle_model_artifact.relpath
        
        if not subtitle_model_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"Subtitle Model file not found: {subtitle_model_path}",
                ),
            )
        
        # 读取 Subtitle Model v1.2
        with open(subtitle_model_path, "r", encoding="utf-8") as f:
            model_data = json.load(f)
        
        # v1.2: 从 utterances 中提取所有 cues
        utterances = model_data.get("utterances", [])
        cues = []
        for utt in utterances:
            cues.extend(utt.get("cues", []))
        
        if not cues:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No cues found in Subtitle Model",
                ),
            )
        
        # 获取 API key
        api_key = get_openai_api_key()
        if not api_key:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="RuntimeError",
                    message="OpenAI API key not found. Please set OPENAI_API_KEY environment variable.",
                ),
            )
        
        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("mt", {})
        model = phase_config.get("model", ctx.config.get("openai_model", "gpt-4o-mini"))
        temperature = phase_config.get("temperature", ctx.config.get("openai_temperature", 0.3))
        cps_limit = float(phase_config.get("cps_limit", ctx.config.get("mt_cps_limit", 15.0)))
        max_retries = int(phase_config.get("max_retries", ctx.config.get("mt_max_retries", 2)))
        use_time_aware = phase_config.get("use_time_aware", ctx.config.get("mt_use_time_aware", True))
        
        # 调用 Processor 层进行时间感知翻译（cue-level）
        try:
            result = mt_run(
                cues=cues,  # 直接传递 cues（来自 Subtitle Model）
                api_key=api_key,
                model=model,
                temperature=temperature,
                cps_limit=cps_limit,
                max_retries=max_retries,
                use_time_aware=use_time_aware,
            )
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=str(e),
                ),
            )
        
        # 从 ProcessorResult 提取翻译结果
        translations = result.data["translations"]
        
        # 构建 cue_id -> translation 映射
        translation_map = {t["cue_id"]: t for t in translations}
        
        # v1.2: 翻译结果不写回 SSOT，单独保存到 translate.context.json
        # 构建翻译上下文（包含翻译结果和 metrics）
        context = {
            "translations": [],
        }
        
        # 生成英文 segments（用于 SRT）
        en_segments = []
        for cue in cues:
            cue_id = cue.get("cue_id", "")
            translation = translation_map.get(cue_id)
            
            if translation and translation.get("text"):
                # 保存翻译结果到 context（不写回 SSOT）
                context["translations"].append({
                    "cue_id": cue_id,
                    "lang": "en",
                    "text": translation["text"],
                    "metrics": {
                        "max_chars": translation["max_chars"],
                        "actual_chars": translation["actual_chars"],
                        "cps": round(translation["cps"], 2),
                    },
                    "provider": "mt_v1",
                    "status": translation["status"],
                })
                
                # 生成英文 segments（用于 SRT）
                en_seg = {
                    "id": cue_id,
                    "start": cue.get("start_ms", 0) / 1000.0,  # 毫秒转秒
                    "end": cue.get("end_ms", 0) / 1000.0,
                    "text": cue.get("source", {}).get("text", ""),  # 保留中文原文
                    "en_text": translation["text"],
                    "speaker": cue.get("speaker", ""),
                }
                en_segments.append(en_seg)
        
        # Phase 层负责文件 IO：写入到 runner 预分配的 outputs.paths
        # translate.context.json（翻译结果单独保存，不写回 SSOT）
        context_path = outputs.get("translate.context")
        context_path.parent.mkdir(parents=True, exist_ok=True)
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)
        info(f"Saved translation context to: {context_path}")
        
        # subs.en.srt
        en_srt_path = outputs.get("subs.en_srt")
        write_srt_from_segments(en_segments, str(en_srt_path), text_key="en_text")
        
        # 返回 PhaseResult：只声明哪些 outputs 成功
        return PhaseResult(
            status="succeeded",
            outputs=[
                "translate.context",
                "subs.en_srt",
            ],
            metrics={
                "cues_count": len(cues),
                "translated_count": len(context["translations"]),
                "ok_count": result.metrics.get("ok_count", 0),
                "compressed_count": result.metrics.get("compressed_count", 0),
                "failed_count": result.metrics.get("failed_count", 0),
                "cps_limit": cps_limit,
            },
        )
