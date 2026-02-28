"""
MT Phase: 机器翻译（只调模型，输出英文整段文本）

职责：
- 按 utterance 粒度翻译
- 输出 mt_input.jsonl 和 mt_output.jsonl
- 不处理时间轴、不生成 SRT
"""
import json
import re
from pathlib import Path
from typing import Dict, List

from dubora.pipeline.core.phase import Phase
from dubora.pipeline.core.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora.pipeline.processors.mt.utterance_translate import (
    pick_k,
    estimate_en_duration_ms,
    translate_utterance_with_retry,
)
from dubora.pipeline.processors.mt.time_aware_impl import create_translate_fn
from dubora.pipeline.processors.mt.name_guard import NameGuard, load_config
from dubora.pipeline.processors.mt.dict_loader import DictLoader
from dubora.pipeline.processors.mt.name_map_complete import (
    complete_names_with_llm,
)
from dubora.config.settings import get_openai_key
from dubora.utils.logger import info, error, warning


def _build_name_variants(en_name: str, src_name: str) -> List[str]:
    """
    构建人名的常见误译变体列表，用于强制替换。

    例如 en_name="Ping An", src_name="平安" →
    ["Ping'an", "Pingan", "ping'an", "Peace", "An'an", "Anan"]
    """
    variants = []
    parts = en_name.split()
    if len(parts) == 2:
        a, b = parts
        variants.append(f"{a}'{b.lower()}")     # Ping'an
        variants.append(f"{a}{b.lower()}")      # Pingan
        variants.append(f"{a.lower()}'{b.lower()}")  # ping'an
        variants.append(f"{a}{b}")              # PingAn
    # 常见语义误译（中文名 → 英文含义）
    semantic = {
        "平安": ["Peace", "Safe", "Safety"],
        "明": ["Bright", "Light"],
        "安": ["An'an", "Anan"],
    }
    for cn_part, words in semantic.items():
        if cn_part in src_name:
            variants.extend(words)
    return variants


class MTPhase(Phase):
    """机器翻译 Phase（只调模型）。"""
    
    name = "mt"
    version = "1.1.0"
    
    def requires(self) -> list[str]:
        """需要 dub.dub_manifest（dub.json SSOT）。"""
        return ["dub.dub_manifest"]
    
    def provides(self) -> list[str]:
        """生成 mt.mt_input, mt.mt_output。"""
        return ["mt.mt_input", "mt.mt_output"]
    
    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 MT Phase。

        流程：
        1. 读取 dub.json（AsrModel SSOT）
        2. 生成 mt_input.jsonl（包含约束信息）
        3. 调用翻译模型
        4. 生成 mt_output.jsonl（只包含英文整段文本）
        """
        # 获取输入（dub.json AsrModel SSOT）
        dub_manifest_artifact = inputs["dub.dub_manifest"]
        dub_manifest_path = Path(ctx.workspace) / dub_manifest_artifact.relpath

        if not dub_manifest_path.exists():
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="FileNotFoundError",
                    message=f"dub.json not found: {dub_manifest_path}",
                ),
            )

        # 读取 dub.json (AsrModel)
        from dubora.schema.asr_model import AsrModel
        with open(dub_manifest_path, "r", encoding="utf-8") as f:
            asr_model = AsrModel.from_dict(json.load(f))

        segments = asr_model.segments

        if not segments:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No segments found in dub.json",
                ),
            )

        # 数据校验：检查 text/text_en 字段是否错位
        for seg in segments:
            if not seg.text.strip() and seg.text_en and re.search(r'[\u4e00-\u9fff]', seg.text_en):
                warning(
                    f"  {seg.id}: text 为空但 text_en 含中文 '{seg.text_en}'，"
                    f"疑似字段错位，自动修正 text←text_en"
                )
                seg.text = seg.text_en
                seg.text_en = ""

        # 构建 utterances 兼容格式（供下游逻辑使用）
        utterances = []
        for seg in segments:
            utterances.append({
                "utt_id": seg.id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "text": seg.text,
                "speaker": {
                    "id": seg.speaker,
                    "gender": seg.gender,
                },
            })

        # 从 dub.json 构建整集上下文
        episode_context_text = " ".join(
            seg.text for seg in segments if seg.text.strip()
        )
        if episode_context_text:
            info(f"Built episode context from dub.json: {len(episode_context_text)} characters")
        else:
            warning("No text found in dub.json segments, proceeding without episode context")

        # 获取配置
        phase_config = ctx.config.get("phases", {}).get("mt", {})
        
        # ============================================================
        # 翻译引擎选择：支持 Gemini 和 OpenAI 两套独立方案
        # ============================================================
        # 配置优先级：
        # 1. phase_config.get("engine") - 显式指定引擎（"gemini" 或 "openai"）
        # 2. phase_config.get("model") - 通过模型名称推断（gemini-* 或 gpt-*）
        # 3. ctx.config.get("mt_engine") - 全局配置
        # 4. 默认：根据模型名称推断，如果都没有则使用 "gemini"
        
        # 方法1：显式指定引擎
        engine = phase_config.get("engine")
        if not engine:
            # 方法2：通过模型名称推断
            model_name = phase_config.get("model", ctx.config.get("mt_model", ""))
            if model_name.startswith("gemini"):
                engine = "gemini"
            elif model_name.startswith("gpt") or model_name.startswith("o1"):
                engine = "openai"
            else:
                # 方法3：全局配置
                engine = ctx.config.get("mt_engine", "gemini")  # 默认使用 Gemini
        
        engine = engine.lower()
        if engine not in ["gemini", "openai"]:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message=f"Invalid translation engine: {engine}. Must be 'gemini' or 'openai'.",
                ),
            )
        
        is_gemini = (engine == "gemini")
        
        # 根据引擎选择模型和 API key
        if is_gemini:
            # Gemini 方案
            from dubora.config.settings import get_gemini_key
            # 默认使用最新的模型（gemini-2.0-flash 或 gemini-1.5-flash）
            model = phase_config.get("model", ctx.config.get("mt_model", ctx.config.get("gemini_model", "gemini-1.5-flash")))
            api_key = phase_config.get("api_key") or get_gemini_key()
            if not api_key:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="Gemini API key not found. Please set GEMINI_API_KEY environment variable.",
                    ),
                )
            default_temp = 0.4
            info(f"Using Gemini translation engine: {model}")
        else:
            # OpenAI 方案
            model = phase_config.get("model", ctx.config.get("mt_model", ctx.config.get("openai_model", "gpt-4o-mini")))
            api_key = phase_config.get("api_key") or get_openai_key()
            if not api_key:
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type="RuntimeError",
                        message="OpenAI API key not found. Please set OPENAI_API_KEY environment variable.",
                    ),
                )
            default_temp = 0.3
            info(f"Using OpenAI translation engine: {model}")
        
        # Fallback 配置（可选，默认关闭）
        # 推荐：保持 fallback_enabled=False，使用单引擎 + 同引擎重试策略
        fallback_enabled = phase_config.get("fallback_enabled", False)
        fallback_api_key = None
        fallback_model = None
        if fallback_enabled:
            # Fallback 使用另一个引擎
            if is_gemini:
                # 主引擎是 Gemini，fallback 使用 OpenAI
                fallback_api_key = phase_config.get("fallback_api_key") or get_openai_key()
                fallback_model = phase_config.get("fallback_model", ctx.config.get("openai_model", "gpt-4o-mini"))
            else:
                # 主引擎是 OpenAI，fallback 使用 Gemini
                from dubora.config.settings import get_gemini_key
                fallback_api_key = phase_config.get("fallback_api_key") or get_gemini_key()
                fallback_model = phase_config.get("fallback_model", ctx.config.get("gemini_model", "gemini-pro"))
            
            if fallback_api_key:
                warning(f"Fallback enabled: {'OpenAI' if is_gemini else 'Gemini'} ({fallback_model}) - Note: This may cause output inconsistency")
            else:
                warning("Fallback enabled but API key not found, disabling fallback")
                fallback_enabled = False
        
        # 温度参数
        temperature = phase_config.get("temperature", ctx.config.get("mt_temperature", default_temp))
        
        max_retries = int(phase_config.get("max_retries", ctx.config.get("mt_max_retries", 3)))
        enable_name_guard = phase_config.get("enable_name_guard", ctx.config.get("mt_enable_name_guard", True))
        enable_name_map = phase_config.get("enable_name_map", ctx.config.get("mt_enable_name_map", True))
        target_locale = phase_config.get("target_locale", ctx.config.get("mt_target_locale", "en-US"))
        
        # 初始化 Name Guard（如果启用）
        name_guard = None
        if enable_name_guard:
            name_guard_config_path = phase_config.get("name_guard_config_path")
            if name_guard_config_path:
                name_guard_config = load_config(Path(name_guard_config_path))
            else:
                name_guard_config = load_config()  # 使用默认路径
            name_guard = NameGuard(name_guard_config)
            info("Name Guard enabled: 人名识别已启用")
        
        # 确定 dict_dir（dub/dict 目录）
        video_path_str = ctx.config.get("video_path")
        if video_path_str:
            video_path = Path(video_path_str)
            dict_dir = video_path.parent / "dub" / "dict"  # dub/dict 目录（如 videos/dbqsfy/dub/dict/）
        else:
            # 降级方案：从 workspace 推导
            # workspace = videos/dbqsfy/dub/1/ → dict_dir = videos/dbqsfy/dub/dict/
            workspace_path = Path(ctx.workspace)
            dict_dir = workspace_path.parent / "dict"  # 跳过 episode_stem，得到 dub/dict 目录
        
        # 初始化字典加载器（统一管理所有字典）
        dict_loader = DictLoader(dict_dir)

        # 将 names.json 中的人名同步到 NameGuard 白名单
        # 这样用户手动添加的人名、LLM 之前识别的人名都能被自动检测
        if name_guard and dict_loader.names:
            synced = 0
            for name in dict_loader.names:
                if name not in name_guard.config.known_names:
                    name_guard.config.known_names.append(name)
                    synced += 1
            if synced:
                info(f"Synced {synced} names from dict to NameGuard whitelist")

        # 兼容旧版 NameMap（如果启用）
        name_map_instance = None
        if enable_name_map:
            # 从 dict_loader 同步 names 到旧版 NameMap（向后兼容）
            # 注意：这里主要是为了兼容现有的 name_map_complete 逻辑
            # 未来可以完全迁移到 DictLoader
            from dubora.pipeline.processors.mt.name_map import NameMap
            names_path = dict_dir / "names.json"
            name_map_instance = NameMap(names_path)
            # 同步 dict_loader.names 到 name_map_instance
            for src_name, target in dict_loader.names.items():
                if not name_map_instance.has(src_name):
                    name_map_instance.add(
                        src_name=src_name,
                        target=target,
                        style="dict",
                        first_seen=ctx.job_id,
                        source="dict",
                    )
            info(f"DictLoader enabled: names={len(dict_loader.names)}, slang={len(dict_loader.slang)}")
        
        # 生成 mt_input.jsonl（先收集所有人名）
        mt_input_path = outputs.get("mt.mt_input")
        mt_input_path.parent.mkdir(parents=True, exist_ok=True)
        
        mt_input_lines = []
        all_src_names = set()  # 收集所有人名（用于批量查询 NameMap）
        # 保存每个 utterance 的 placeholder_to_name 映射，用于后续替换
        utt_placeholder_map: Dict[str, Dict[str, str]] = {}  # {utt_id: {placeholder: src_name}}
        
        for utterance in utterances:
            utt_id = utterance.get("utt_id", "")
            start_ms = utterance.get("start_ms", 0)
            end_ms = utterance.get("end_ms", 0)
            # 直接使用 utterance 的 text 字段（dub.json SSOT）
            zh_text = utterance.get("text", "").strip()
            if not zh_text:
                warning(f"  {utt_id}: utterance.text 为空，跳过")
                continue
            
            # Name Guard - 提取并替换人名
            placeholder_to_name = {}  # {placeholder: src_name}（临时变量，用于收集 src_names）
            if name_guard and zh_text:
                zh_text, name_map_dict = name_guard.extract_and_replace_names(zh_text)
                # name_map_dict 已经是 {placeholder: src_name}，例如：{"<<NAME_0>>": "平安"}
                placeholder_to_name = name_map_dict
                if placeholder_to_name:
                    all_src_names.update(placeholder_to_name.values())
                    info(f"  {utt_id}: 识别到 {len(placeholder_to_name)} 个人名: {list(placeholder_to_name.values())}")
                    # 保存映射，用于后续替换
                    utt_placeholder_map[utt_id] = placeholder_to_name
            
            # 输入格式化 - 将占位符格式化为 <<NAME_0:平安>> 格式（让模型能看到人名信息）
            # 注意：这里不替换为英文名，模型输出会保留 <<NAME_0>> 占位符
            # 占位符已经是自描述的，不需要额外的 name_map 字段
            if placeholder_to_name:
                for placeholder, src_name in placeholder_to_name.items():
                    # 使用 <<NAME_0:平安>> 格式（让模型知道这是人名，但输出时保留占位符）
                    annotated_placeholder = f"<<{placeholder[2:-2]}:{src_name}>>"  # <<NAME_0>> -> <<NAME_0:平安>>
                    zh_text = zh_text.replace(placeholder, annotated_placeholder)
            
            # 实时计算 zh_tps（中文字数 / 秒）
            window_ms = end_ms - start_ms
            zh_char_count = sum(1 for c in zh_text if '\u4e00' <= c <= '\u9fff')
            zh_tps = zh_char_count / (window_ms / 1000.0) if window_ms > 0 else 0.0
            from dubora.pipeline.processors.mt.utterance_translate import pick_k
            k = pick_k(zh_tps)
            budget_ms = window_ms * k

            mt_input_lines.append({
                "utt_id": utt_id,
                "source": {
                    "lang": "zh",
                    "text": zh_text,  # 自描述占位符：<<NAME_0:平安>>，slang 标记：<SLANG:key>
                },
                "constraints": {
                    "window_ms": window_ms,
                    "zh_tps": zh_tps,
                    "k": k,
                    "budget_ms": budget_ms,
                },
            })
        
        # 创建翻译函数（单引擎 + 可选 fallback）
        try:
            translate_fn = create_translate_fn(
                api_key=api_key,
                model=model,
                temperature=temperature,
                fallback_enabled=fallback_enabled,
                fallback_api_key=fallback_api_key,
                fallback_model=fallback_model,
            )
        except Exception as e:
            error_msg = f"Failed to create translation function: {e}"
            error(error_msg)
            # 如果是 Gemini 引擎失败，提供切换到 OpenAI 的建议
            if is_gemini:
                error("")
                error("=" * 60)
                error("Gemini engine failed. To switch to OpenAI:")
                error("  Set in config: phases.mt.engine = 'openai'")
                error("  Or set environment: mt_engine=openai")
                error("=" * 60)
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type=type(e).__name__,
                    message=error_msg,
                ),
            )
        
        # Phase B: LLM 补全缺失项（只针对未命中的名字，first-write-wins）
        # 统一使用与翻译相同的模型（Gemini 或 OpenAI）
        if all_src_names:
            missing_names = [name for name in all_src_names if not dict_loader.has_name(name)]
            if missing_names:
                info(f"DictLoader 缺失 {len(missing_names)} 个人名，调用 LLM 补全: {missing_names}")
                
                # 调用 LLM 补全（使用与翻译相同的模型）
                llm_results = complete_names_with_llm(
                    missing_names=missing_names,
                    translate_fn=translate_fn,  # 使用统一的翻译函数
                    is_gemini=is_gemini,  # 传入模型类型
                )
                
                # Phase D: 添加到 DictLoader（first-write-wins：立刻写入并锁死）
                for src_name, result in llm_results.items():
                    success = dict_loader.add_name(src_name, result["target"])
                    if success:
                        info(f"  DictLoader 添加: '{src_name}' -> '{result['target']}' (style: {result['style']})")
                    else:
                        # 已存在（不应该发生，但防御性检查）
                        warning(f"  DictLoader 已存在: '{src_name}'，跳过添加")
                
                # 保存 names.json
                dict_loader.save_names()
                info(f"DictLoader names.json 已保存: {len(dict_loader.names)} 个条目")
                
                # 同步到旧版 NameMap（向后兼容）
                if name_map_instance:
                    for src_name, result in llm_results.items():
                        if dict_loader.has_name(src_name):
                            target = dict_loader.resolve_name(src_name)
                            if target:
                                name_map_instance.add(
                                    src_name=src_name,
                                    target=target,
                                    style=result["style"],
                                    first_seen=ctx.job_id,
                                    source="llm",
                                )
                    name_map_instance.save()
        
        # 写入 mt_input.jsonl
        with open(mt_input_path, "w", encoding="utf-8") as f:
            for line in mt_input_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        info(f"Saved mt_input.jsonl: {len(mt_input_lines)} utterances")
        
        # 名字一致性缓存（first-write-wins，内存级）
        # key: 中文名（如 "平安"），value: 英文名（如 "Ping An"）
        name_en_cache: Dict[str, str] = {}
        
        # 从 DictLoader 预加载已有翻译
        for src_name in all_src_names:
            target = dict_loader.resolve_name(src_name)
            if target:
                name_en_cache[src_name] = target
                info(f"  DictLoader 预加载: '{src_name}' -> '{target}'")
        
        # 获取剧情简介（可选，从配置读取）
        plot_overview = phase_config.get("plot_overview", "")
        if not plot_overview:
            # 默认剧情简介（可以后续从配置或元数据读取）
            plot_overview = "于平安蒙冤背负杀亲罪名，在狱中度过十年。出狱后，他决心查明真相，洗刷冤屈。"
        
        # 翻译每个 utterance
        mt_output_lines = []
        ok_count = 0
        failed_count = 0
        total_retries = 0
        
        for mt_input in mt_input_lines:
            utt_id = mt_input["utt_id"]
            zh_text = mt_input["source"]["text"]
            budget_ms = mt_input["constraints"]["budget_ms"]
            
            if not zh_text:
                # 空文本，跳过
                mt_output_lines.append({
                    "utt_id": utt_id,
                    "target": {
                        "lang": "en",
                        "text": "",
                    },
                    "stats": {
                        "en_est_ms": 0.0,
                        "budget_ms": budget_ms,
                        "retries": 0,
                    },
                })
                continue
            
            # 翻译（带重试 + 轻量 glossary 校验）
            # 按需注入：只注入当前句命中的 glossary 条目
            slang_glossary_text = dict_loader.get_glossary_hits(zh_text)
            try:
                en_text, retries = translate_utterance_with_retry(
                    zh_text=zh_text,
                    budget_ms=budget_ms,
                    translate_fn=translate_fn,
                    max_retries=max_retries,
                    episode_context=episode_context_text,
                    plot_overview=plot_overview,
                    slang_glossary_text=slang_glossary_text,
                    dict_loader=dict_loader,  # 传入用于校验
                    is_gemini=is_gemini,  # 传入模型类型
                )
            except Exception as e:
                error_msg = (
                    f"Translation failed for utterance {utt_id}: {e}\n"
                    f"Source text: {zh_text[:200]}"
                )
                error(error_msg)
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(
                        type=type(e).__name__,
                        message=error_msg,
                    ),
                )
            
            # 轻量 glossary 校验（如果违反，重试一次）
            if dict_loader.slang:
                violations = dict_loader.check_glossary_violation(zh_text, en_text)
                if violations and retries < max_retries:
                    warning(
                        f"  {utt_id}: Glossary violation detected: {violations}. "
                        f"Retrying with stricter prompt..."
                    )
                    # 重试一次，使用更严格的 prompt
                    from typing import List
                    en_text_retry, _ = translate_utterance_with_retry(
                        zh_text=zh_text,
                        budget_ms=budget_ms,
                        translate_fn=translate_fn,
                        max_retries=1,  # 只重试一次
                        episode_context=episode_context_text,
                        plot_overview=plot_overview,
                        slang_glossary_text=slang_glossary_text,
                        dict_loader=dict_loader,
                        is_retry=True,  # 标记为重试
                        violations=violations,  # 传入违反的术语
                        is_gemini=is_gemini,  # 传入模型类型
                    )
                    if en_text_retry:
                        en_text = en_text_retry
                        retries += 1
                        info(f"  {utt_id}: Retry successful after glossary violation")
            
            # 在清理之前，先替换占位符为实际人名
            # 如果模型输出了占位符（<<NAME_0>> 或 <<NAME_0:平安>>），需要替换为英文名
            placeholder_to_name = utt_placeholder_map.get(utt_id, {})
            if placeholder_to_name and en_text:
                import re
                # 匹配 <<NAME_0>> 或 <<NAME_0:平安>> 格式的占位符
                placeholder_pattern = r'<<NAME_(\d+)(?::[^>]*)?>>'
                matches = list(re.finditer(placeholder_pattern, en_text))
                if matches:
                    # 从后往前替换（避免位置偏移）
                    for match in reversed(matches):
                        placeholder_index = int(match.group(1))
                        placeholder = f"<<NAME_{placeholder_index}>>"
                        # 从 placeholder_to_name 中查找对应的中文名
                        if placeholder in placeholder_to_name:
                            src_name = placeholder_to_name[placeholder]
                            # 从 name_en_cache 或 dict_loader 中查找英文名
                            en_name = name_en_cache.get(src_name)
                            if not en_name:
                                en_name = dict_loader.resolve_name(src_name)
                            if en_name:
                                # 替换占位符为英文名
                                en_text = en_text[:match.start()] + en_name + en_text[match.end():]
                                info(f"  {utt_id}: 替换占位符 {placeholder} ({src_name}) -> {en_name}")
                            else:
                                # 如果找不到英文名，这是严重错误，应该报错
                                error_msg = (
                                    f"占位符 {placeholder} ({src_name}) 未找到英文名。"
                                    f"这不应该发生，因为所有名字都应该在翻译前被补全。"
                                    f"请检查 DictLoader 和 name_en_cache。"
                                )
                                error(error_msg)
                                return PhaseResult(
                                    status="failed",
                                    error=ErrorInfo(
                                        type="RuntimeError",
                                        message=error_msg,
                                    ),
                                )
                        else:
                            # 占位符不在 placeholder_to_name 中，这也不应该发生
                            warning(f"  {utt_id}: 占位符 {placeholder} 不在 placeholder_to_name 映射中")
            
            # 自动清理：移除所有系统标记（防御性，即使模型输出了也要清理）
            from dubora.pipeline.processors.mt.utterance_translate import clean_translation_output, is_only_punctuation
            en_text = clean_translation_output(en_text)

            # 人名强制替换：如果模型忽略占位符指令，直接翻译了人名（如 Peace / An'an / Ping'an），
            # 用字典中的正确英文名强制覆盖。
            if placeholder_to_name and en_text:
                for ph, src_name in placeholder_to_name.items():
                    en_name = name_en_cache.get(src_name) or dict_loader.resolve_name(src_name)
                    if not en_name:
                        continue
                    if en_name.lower() in en_text.lower():
                        continue  # 已包含正确名字

                    # 输出中不包含正确的英文名 → 尝试替换常见误译变体
                    wrong_variants = _build_name_variants(en_name, src_name)
                    replaced = False
                    for variant in wrong_variants:
                        pattern = re.compile(re.escape(variant), re.IGNORECASE)
                        if pattern.search(en_text):
                            en_text = pattern.sub(en_name, en_text)
                            info(f"  {utt_id}: 人名强制替换 '{variant}' -> '{en_name}'")
                            replaced = True
                            break

                    if not replaced:
                        # 短句兜底：如果整句只有 1-2 个词（纯人名 utterance），直接替换
                        stripped = en_text.strip().rstrip(".,!?;:")
                        if len(stripped.split()) <= 2:
                            punct = en_text[len(en_text.rstrip(".,!?;:")):] or "."
                            en_text = en_name + punct
                            info(f"  {utt_id}: 人名强制替换（短句兜底）'{stripped}' -> '{en_name}'")

            # 兜底：如果该 utterance 含有人名占位符，但清理后只剩标点/空白，
            # 说明上游模型输出质量极差；至少保证字典人名能带入，避免生成 ", !" 这种结果。
            if placeholder_to_name and (not en_text or is_only_punctuation(en_text)):
                # 保持占位符顺序（NAME_0, NAME_1 ...），并去重
                def _placeholder_index(ph: str) -> int:
                    # ph: "<<NAME_0>>"
                    import re
                    m = re.search(r"<<NAME_(\d+)>>", ph)
                    return int(m.group(1)) if m else 0

                ordered_placeholders = sorted(
                    placeholder_to_name.keys(),
                    key=_placeholder_index,
                )
                seen = set()
                en_names = []
                for ph in ordered_placeholders:
                    src_name = placeholder_to_name.get(ph, "")
                    if not src_name or src_name in seen:
                        continue
                    seen.add(src_name)
                    en_name = name_en_cache.get(src_name) or dict_loader.resolve_name(src_name) or ""
                    if en_name:
                        en_names.append(en_name)
                base = ", ".join(en_names).strip()
                if base:
                    # 简单称呼词兜底
                    suffix = ""
                    if "哥" in zh_text:
                        suffix = "bro"
                    elif "姐" in zh_text:
                        suffix = "sis"
                    if suffix:
                        base = f"{base}, {suffix}"
                    end_punct = "!" if ("！" in zh_text or "!" in zh_text) else "."
                    en_text = f"{base}{end_punct}"
                    info(f"  {utt_id}: MT 输出仅标点，兜底用字典名生成: {en_text}")
            
            # 反作弊校验：确保输出没有占位符、<sep>、中文
            import re

            # 中文残留：重试一次，还不行就剥掉
            if re.search(r"[\u4e00-\u9fff]", en_text):
                warning(f"  {utt_id}: Output contains Chinese, retrying: {en_text[:120]}")
                en_text_retry, _ = translate_utterance_with_retry(
                    zh_text=zh_text,
                    budget_ms=budget_ms,
                    translate_fn=translate_fn,
                    max_retries=1,
                    episode_context=episode_context_text,
                    plot_overview=plot_overview,
                    slang_glossary_text=slang_glossary_text,
                    is_gemini=is_gemini,
                )
                if en_text_retry and not re.search(r"[\u4e00-\u9fff]", en_text_retry):
                    en_text = clean_translation_output(en_text_retry)
                    info(f"  {utt_id}: Retry succeeded, Chinese removed")
                else:
                    en_text = re.sub(r"[\u4e00-\u9fff]+", "", en_text).strip()
                    en_text = re.sub(r"\s+", " ", en_text).strip()
                    warning(f"  {utt_id}: Stripped remaining Chinese, result: {en_text[:120]}")

            # 占位符 / <sep> 残留：直接清理（不需要重试，clean 函数能处理）
            if re.search(r"<<NAME_\d+", en_text) or "<sep>" in en_text:
                warning(f"  {utt_id}: Output still has placeholders/<sep>, force cleaning: {en_text[:120]}")
                en_text = clean_translation_output(en_text)
            
            # 估算英文时长
            en_est_ms = estimate_en_duration_ms(en_text)
            
            if en_text:
                ok_count += 1
            else:
                failed_count += 1
            
            total_retries += retries
            
            # mt_output.jsonl = 最终用户可见文本
            # 模型必须一次性产出"干净英文"，不允许占位符
            # 反作弊校验已在 translate_utterance_with_retry 中完成
            
            mt_output_lines.append({
                "utt_id": utt_id,
                "target": {
                    "lang": "en",
                    "text": en_text,  # 纯英文，无占位符（已通过反作弊校验）
                },
                "stats": {
                    "en_est_ms": en_est_ms,
                    "budget_ms": budget_ms,
                    "retries": retries,
                },
            })
        
        # 写入 mt_output.jsonl（写入前再次校验）
        mt_output_path = outputs.get("mt.mt_output")
        mt_output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 最终防御：剥掉任何残留的系统痕迹（不再 assert 挂掉）
        import re
        for line in mt_output_lines:
            en_text = line.get("target", {}).get("text", "")
            if not en_text:
                continue
            dirty = False
            if "<<NAME_" in en_text or "<sep>" in en_text:
                en_text = clean_translation_output(en_text)
                dirty = True
            if re.search(r"[\u4e00-\u9fff]", en_text):
                en_text = re.sub(r"[\u4e00-\u9fff]+", "", en_text).strip()
                en_text = re.sub(r"\s+", " ", en_text).strip()
                dirty = True
            if dirty:
                warning(f"  Final cleanup for {line['utt_id']}: {en_text[:120]}")
                line["target"]["text"] = en_text
        
        with open(mt_output_path, "w", encoding="utf-8") as f:
            for line in mt_output_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        info(f"Saved mt_output.jsonl: {len(mt_output_lines)} utterances (all validated: no placeholders, no <sep>, no <SLANG>, no Chinese)")
        
        # 返回 PhaseResult
        return PhaseResult(
            status="succeeded",
            outputs=[
                "mt.mt_input",
                "mt.mt_output",
            ],
            metrics={
                "utterances_count": len(utterances),
                "ok_count": ok_count,
                "failed_count": failed_count,
                "total_retries": total_retries,
            },
        )
