#!/usr/bin/env python3
"""
火山引擎 AI 视频翻译（声影智译）测试脚本

根据文档：https://www.volcengine.com/docs/4/1584290?lang=zh

功能：
- 提交 AI 视频翻译任务（字幕、声音、口型）
- 查询任务状态
- 获取翻译结果

使用方法：
    python test/volcengine_ai_translation.py --vid <video_id> --source-lang zh --target-lang en
"""
import argparse
import json
import sys
import time
from pathlib import Path

try:
    from volcengine.base.Service import Service
    from volcengine.ServiceInfo import ServiceInfo
    from volcengine.ApiInfo import ApiInfo
    from volcengine.Credentials import Credentials
except ImportError:
    print("导入 volcengine 失败；请先安装官方 SDK：")
    print("  pip install volcengine")
    print("并参考文档配置 AK/SK: https://www.volcengine.com/docs/4640/78985")
    sys.exit(1)

import os
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pikppo.config.settings import load_env_file
from pikppo.utils.logger import info, warning, error

# 自动加载 .env 文件（如果存在）
load_env_file()


# ============================================================================
# 配置字典 - 可以手动修改这些参数
# ============================================================================

# TranslationConfig 配置
# 参考文档：https://www.volcengine.com/docs/4/1584290?lang=zh#translationconfig
TRANSLATION_CONFIG = {
    # 源语言（String, 必需）
    # 可选值: "zh"（中文）, "en"（英文）
    "SourceLanguage": "zh",  # 默认中文
    
    # 目标语言（String, 必需）
    "TargetLanguage": "en",  # 默认英文
    
    # 翻译类型列表（Array of String, 必需）
    # 可选组合:
    #   ["SubtitleTranslation"]: 仅文本翻译
    #   ["SubtitleTranslation", "VoiceTranslation"]: 文本和语音翻译
    #   ["SubtitleTranslation", "VoiceTranslation", "FacialTranslation"]: 文本、语音和面容翻译
    "TranslationTypeList": ["SubtitleTranslation", "VoiceTranslation"],
    
    # 术语库配置（AITranslationTermbaseConfig, 可选）
    # 用于在翻译过程中应用自定义的术语表，确保特定词汇（如品牌名、产品名、专有名词等）的翻译准确性和一致性
    "TermbaseConfig": None,  # 例如: {"TermbaseId": "your-termbase-id"}
}

# AITranslationProcessConfig 配置
# 参考文档：https://www.volcengine.com/docs/4/1584290?lang=zh#translationconfig
PROCESS_CONFIG = {
    # 执行过程中需要暂停的阶段列表
    # 可选值：["SubtitleRecognition"], ["SubtitleTranslation"]
    "SuspensionStageList": None,  # 例如: ["SubtitleRecognition"]
    
    # 是否禁用分场景音色复刻
    # true: 合并音色，为每个说话人生成统一音色（如 Speaker 1）
    # false: 分场景保留音色，保留同一说话人在不同场景下的音色差异（如 Speaker 1-0, Speaker 1-1）
    "DisableCloneVoiceByScene": False,  # 默认 false（分场景保留音色）
    
    # 是否禁用字幕断句优化（字幕打轴）
    # false: 自动根据标点符号将过长的字幕分割成短句（默认）
    # true: 禁用此功能，严格按照原始时间轴进行断句
    "DisableSubtitlePunctSplit": False,  # 默认 false（启用断句优化）
    
    # 是否禁用智能字幕改写
    # false: 利用大模型对翻译后的字幕进行智能优化（默认）
    # true: 禁用此功能，输出机器翻译的原始结果
    "DisableSmartSubtitleRewrite": False,  # 默认 false（启用智能改写）
    
    # 是否启用口型同步
    "EnableLipSync": True,  # 默认启用
    
    # 是否启用声音克隆
    "EnableVoiceClone": True,  # 默认启用
}

# AITranslationVoiceCloneConfig 配置
# 参考文档：https://www.volcengine.com/docs/4/1584290?lang=zh#translationconfig
VOICE_CLONE_CONFIG = {
    # 背景音音量（Integer, 可选）
    # 用于调节最终合成视频中背景音的音量大小
    # 默认值为 100，表示保持原始背景音音量不变
    # 取值范围: [0, 100]
    # 设置为 0 表示背景音完全静音
    "BackgroundVolume": 100,  # 0-100，默认 100（保持原始音量）
}

# AITranslationSubtitleRecognitionConfig 配置
# 参考文档：https://www.volcengine.com/docs/4/1584290?lang=zh#translationconfig
SUBTITLE_RECOGNITION_CONFIG = {
    # 字幕来源（String, 必需）
    # 可选值:
    #   "OCR": 从视频的画面中识别文字并生成字幕（默认）
    #   "ASR": 从视频的音轨中识别文字并生成字幕
    #   "SourceSubtitleFile": 使用您提供的源语言字幕文件（需配置 SourceSubtitleFileName）
    #   "SourceAndTargetSubtitleFile": 使用您提供的源语言和目标语言字幕文件（需配置 SourceSubtitleFileName 和 TargetSubtitleFileName）
    #   "BilingualSubtitleFile": 使用您提供的双语字幕文件（需配置 BilingualSubtitleFileName）
    #   "SubtitleFile": (已废弃) 行为等同于 SourceSubtitleFile
    "RecognitionType": "OCR",  # 默认 OCR（从画面识别字幕）
    
    # 源语言字幕文件的 FileName（String, 可选）
    # 当 RecognitionType 为 "SourceSubtitleFile" 或 "SourceAndTargetSubtitleFile" 时必填
    # 字幕文件必须预先上传至项目所在的点播空间，支持 WebVTT、SRT 格式
    "SourceSubtitleFileName": None,  # 例如: "source_subtitle.vtt"
    
    # 目标语言字幕文件的 FileName（String, 可选）
    # 当 RecognitionType 为 "SourceAndTargetSubtitleFile" 时必填
    # 字幕文件必须预先上传至项目所在的点播空间，支持 WebVTT、SRT 格式
    "TargetSubtitleFileName": None,  # 例如: "target_subtitle.vtt"
    
    # 双语字幕文件的 FileName（String, 可选）
    # 当 RecognitionType 为 "BilingualSubtitleFile" 时必填
    # 字幕文件必须预先上传至项目所在的点播空间，支持 WebVTT、SRT 格式
    "BilingualSubtitleFileName": None,  # 例如: "dual_subtitle.vtt"
    
    # 是否开启视频理解（Boolean, 可选）
    # true: 系统将综合理解视频画面和语音内容来生成字幕，识别结果更精准但耗时较长
    # false: (默认) 不开启视频理解
    # 注意：此模式需提交工单联系火山引擎技术支持团队申请加入白名单后方可使用
    "IsVision": False,  # 默认 false
}

# AITranslationSubtitleConfig 配置
# 参考文档：https://www.volcengine.com/docs/4/1584290?lang=zh#translationconfig
#
# 配置说明（竖屏短剧 720×1280 场景）：
# - 原中文字幕在"居中偏下"，会做字幕擦除
# - 英文字幕应放在底部稳定区域，避开原字幕擦除区
# - 这是短剧出海的标准做法
#
# 当原中文字幕位于画面中部或偏下时，应将擦除后的英文字幕统一放置于画面底部的稳定区域，
# 以避免残影干扰并符合观众阅读习惯。
SUBTITLE_CONFIG = {
    # 字幕格式（可选）
    "SubtitleFormat": "srt",  # 可选值: "srt", "vtt"
    
    # 是否为硬字幕（Boolean, 必需）
    # true: 字幕将直接嵌入视频画面中，不可关闭或调整
    # false: 软字幕
    "IsHardSubtitle": True,  # 默认 true（硬字幕，用于烧录）
    
    # 是否擦除视频中的原有字幕（Boolean, 必需）
    # true: 系统将在生成新字幕前擦除视频中的原有字幕
    # false: 不擦除原有字幕
    # 注意：字幕擦除为收费功能，按实际输出视频时长计费
    "IsEraseSource": True,  # 默认 true（擦除原有字幕）
    
    # 硬字幕配置（当 IsHardSubtitle 为 true 时，以下参数必填）
    # 
    # 竖屏 720×1280 场景下的最优配置：
    # - 英文字幕放在底部（MarginV = 0.05），避开原字幕擦除区
    # - 字号 18 + 左右 0.07 是 720p 英文最优解（可读性 + 翻译空间 + 不爆行）
    # - 双行显示（ShowLines = 2），最大字符数约 64-68 chars
    
    # 硬字幕的字体大小，单位为像素（Integer, 可选）
    # 取值范围: [1, 80]
    # 当 IsHardSubtitle 为 true 时，此参数必填
    # 720p 竖屏场景推荐: 18
    "FontSize": 18,  # 720p 英文最优解
    
    # 硬字幕距离视频左侧的距离比例（Double, 可选）
    # 取值范围: [0, 1)
    # 当 IsHardSubtitle 为 true 时，此参数必填
    # 720p 竖屏场景推荐: 0.07
    "MarginL": 0.07,  # 720p 英文最优解
    
    # 硬字幕距离视频右侧的距离比例（Double, 可选）
    # 取值范围: [0, 1)
    # 当 IsHardSubtitle 为 true 时，此参数必填
    # 720p 竖屏场景推荐: 0.07
    "MarginR": 0.07,  # 720p 英文最优解
    
    # 硬字幕距离视频底部的距离比例（Double, 可选）
    # 取值范围: [0, 1)
    # 当 IsHardSubtitle 为 true 时，此参数必填
    # 竖屏场景推荐: 0.05（底部安全线，避开 UI，又不会太高）
    # 0.0 容易被进度条、按钮压住
    # 0.05 ≈ 64px（在 720×1280 下），足够避开底部 UI
    "MarginV": 0.05,  # 底部字幕安全线
    
    # 硬字幕最多显示的行数（Integer, 可选）
    # 取值为 0 时表示不限制行数
    # 当 IsHardSubtitle 为 true 时，此参数必填
    # 推荐: 2（双行显示，最大字符数约 64-68 chars）
    "ShowLines": 2,  # 双行显示
}

# 字幕配置对应关系（与 MT/TTS 策略对齐）：
# - 字幕 CPS: 严格 14，放宽 16
# - 双行最大字符: 64-68 chars
# - TTS 加速: ≤ 1.15
# - TTS end_ms 延长: ≤ 300ms，不与下一句重叠
#
# 这是字幕在底部时的最优组合（已应用到上面的 SUBTITLE_CONFIG）


def load_keys():
    """加载环境变量中的密钥。"""
    ak = os.getenv("VOLC_ACCESS_KEY")
    sk = os.getenv("VOLC_SECRET_KEY")
    
    if not ak or not sk:
        error("VOLC_ACCESS_KEY 和 VOLC_SECRET_KEY 环境变量未设置")
        print("\n解决方案：")
        print("1. 在 .env 文件中设置：")
        print("   VOLC_ACCESS_KEY=你的AccessKey")
        print("   VOLC_SECRET_KEY=你的SecretKey")
        print("\n2. 或通过环境变量设置：")
        print("   export VOLC_ACCESS_KEY=你的AccessKey")
        print("   export VOLC_SECRET_KEY=你的SecretKey")
        sys.exit(1)
    
    return ak, sk


def build_vod_client(ak: str, sk: str, region: str = "cn-north-1"):
    """
    构建视频点播（VOD）客户端。
    
    Args:
        ak: Access Key
        sk: Secret Key
        region: 区域（默认 cn-north-1）
    
    Returns:
        Service 客户端
    """
    service = "vod"
    host = "vod.volcengineapi.com"
    
    service_info = ServiceInfo(
        host=host,
        header={"Content-Type": "application/json"},
        credentials=Credentials(ak, sk, service, region),
        connection_timeout=10,
        socket_timeout=10,
        scheme="https",
    )
    
    api_info = {
        "SubmitAITranslationWorkflow": ApiInfo(
            method="POST",
            path="/",
            query={"Action": "SubmitAITranslationWorkflow", "Version": "2025-01-01"},
            form={},
            header={},
        ),
        "GetAITranslationProject": ApiInfo(
            method="POST",
            path="/",
            query={"Action": "GetAITranslationProject", "Version": "2025-01-01"},
            form={},
            header={},
        ),
    }
    
    client = Service(service_info, api_info)
    return client


def submit_translation_task(
    client: Service,
    space_name: str,
    vid: str,
    source_lang: str = None,
    target_lang: str = None,
    translation_types: list[str] = None,
    subtitle_config: dict = None,
    process_config: dict = None,
    termbase_config: dict = None,
) -> dict:
    """
    提交 AI 视频翻译任务。
    
    Args:
        client: VOD 客户端
        space_name: VOD 空间名称
        vid: 视频 ID（Vid）
        source_lang: 源语言（如 "zh", "en"）
        target_lang: 目标语言（如 "en", "ja"）
        translation_types: 翻译类型列表，可选值：
            - "SubtitleTranslation": 字幕翻译
            - "VoiceTranslation": 语音翻译（包括声音和口型）
        subtitle_config: 字幕配置（AITranslationSubtitleConfig），可选字段：
            - SourceSubtitleFileName: 源字幕文件名
            - TargetSubtitleFileName: 目标字幕文件名
            - BilingualSubtitleFileName: 双语字幕文件名
            - SubtitleFormat: 字幕格式（如 "srt", "vtt"）
        process_config: 执行配置（AITranslationProcessConfig），可选字段：
            - EnableLipSync: 是否启用口型同步（Boolean）
            - EnableVoiceClone: 是否启用声音克隆（Boolean）
            - BackgroundVolume: 背景音音量（Integer, 0-100）
    
    Returns:
        项目基础信息（包含 ProjectId 和 ProjectVersion）
    
    参考文档：https://www.volcengine.com/docs/4/1584290?lang=zh#translationconfig
    
    配置对象说明：
    
    TranslationConfig (必需):
        - SourceLanguage: 源语言（String, 必需）
        - TargetLanguage: 目标语言（String, 必需）
        - TranslationTypeList: 翻译类型列表（Array, 必需）
            - "SubtitleTranslation": 字幕翻译
            - "VoiceTranslation": 语音翻译（包括声音和口型）
    
    OperatorConfig (必需):
        - SubtitleConfig: 字幕配置（AITranslationSubtitleConfig, 可选）
        - ProcessConfig: 执行配置（AITranslationProcessConfig, 可选）
    
    AITranslationSubtitleConfig (可选):
        - SourceSubtitleFileName: 源字幕文件名（String）
        - TargetSubtitleFileName: 目标字幕文件名（String）
        - BilingualSubtitleFileName: 双语字幕文件名（String）
        - SubtitleFormat: 字幕格式（String, 如 "srt", "vtt"）
    
    AITranslationProcessConfig (可选):
        - EnableLipSync: 是否启用口型同步（Boolean）
        - EnableVoiceClone: 是否启用声音克隆（Boolean）
        - BackgroundVolume: 背景音音量（Integer, 0-100，默认 100）
    """
    # 使用脚本顶部定义的默认配置（全局变量），函数参数可以覆盖
    current_module = sys.modules[__name__]
    translation_cfg = getattr(current_module, 'TRANSLATION_CONFIG', {})
    
    # 使用配置字典中的值，但允许函数参数覆盖
    final_source_lang = source_lang or translation_cfg.get("SourceLanguage", "zh")
    final_target_lang = target_lang or translation_cfg.get("TargetLanguage", "en")
    final_translation_types = translation_types or translation_cfg.get("TranslationTypeList", ["SubtitleTranslation", "VoiceTranslation"])
    final_termbase_config = termbase_config or translation_cfg.get("TermbaseConfig")
    
    # 构建 TranslationConfig
    translation_config = {
        "SourceLanguage": final_source_lang,
        "TargetLanguage": final_target_lang,
        "TranslationTypeList": final_translation_types,
    }
    
    # 添加 TermbaseConfig（如果提供）
    if final_termbase_config:
        translation_config["TermbaseConfig"] = final_termbase_config
    
    # 构建 OperatorConfig
    # 根据文档：AITranslationOperatorConfig 包含：
    # - SubtitleRecognitionConfig (可选)
    # - VoiceCloneConfig (可选)
    # - SubtitleConfig (可选，文档中单独列出)
    # - ProcessConfig (可选，文档中单独列出)
    operator_config = {}
    
    # 使用脚本顶部定义的默认配置（全局变量），函数参数可以覆盖
    # 构建 SubtitleRecognitionConfig（过滤掉 None 值）
    subtitle_recognition_cfg = getattr(current_module, 'SUBTITLE_RECOGNITION_CONFIG', {})
    final_subtitle_recognition_config = {}
    for k, v in subtitle_recognition_cfg.items():
        # 保留所有非 None 的值（包括 False, 0, 0.0, "" 等）
        if v is not None:
            final_subtitle_recognition_config[k] = v
    if final_subtitle_recognition_config:
        operator_config["SubtitleRecognitionConfig"] = final_subtitle_recognition_config
    
    # 构建 ProcessConfig（过滤掉 None 值）
    if process_config:
        # 如果提供了 process_config，直接使用
        final_process_config = {k: v for k, v in process_config.items() if v is not None}
    else:
        # 否则使用脚本顶部定义的默认配置
        process_cfg = getattr(current_module, 'PROCESS_CONFIG', {})
        final_process_config = {k: v for k, v in process_cfg.items() if v is not None}
    
    if final_process_config:
        operator_config["ProcessConfig"] = final_process_config
    
    # 构建 VoiceCloneConfig（直接在 OperatorConfig 下，不嵌套在 ProcessConfig 中）
    # 根据文档：VoiceCloneConfig 在 OperatorConfig 下，用于声音克隆与合成配置
    voice_clone_cfg = getattr(current_module, 'VOICE_CLONE_CONFIG', {})
    if voice_clone_cfg:
        voice_clone_config = {k: v for k, v in voice_clone_cfg.items() if v is not None}
        if voice_clone_config:
            operator_config["VoiceCloneConfig"] = voice_clone_config
    
    # 构建 SubtitleConfig（过滤掉 None 值，但保留 False 和 0 等有效值）
    if subtitle_config:
        # 如果提供了 subtitle_config，直接使用
        final_subtitle_config = {}
        for k, v in subtitle_config.items():
            if v is not None:
                final_subtitle_config[k] = v
    else:
        # 否则使用脚本顶部定义的默认配置
        subtitle_cfg = getattr(current_module, 'SUBTITLE_CONFIG', {})
        final_subtitle_config = {}
        for k, v in subtitle_cfg.items():
            # 保留所有非 None 的值（包括 False, 0, 0.0, "" 等）
            if v is not None:
                final_subtitle_config[k] = v
    
    if final_subtitle_config:
        operator_config["SubtitleConfig"] = final_subtitle_config
    
    body = {
        "SpaceName": space_name,
        "Vid": vid,
        "TranslationConfig": translation_config,
        "OperatorConfig": operator_config,
    }
    
    info(f"[Submit] SpaceName={space_name}, Vid={vid}, {source_lang}→{target_lang}, Types={translation_types}")
    
    try:
        resp = client.json("SubmitAITranslationWorkflow", {}, json.dumps(body))
        result = json.loads(resp)
        
        if "Result" in result and "ProjectBaseInfo" in result["Result"]:
            project_info = result["Result"]["ProjectBaseInfo"]
            project_id = project_info.get("ProjectId", "")
            project_version = project_info.get("ProjectVersion", "")
            info(f"[Submit] Success, ProjectId={project_id}, ProjectVersion={project_version}")
            return project_info
        else:
            error(f"[Submit] Failed: {result}")
            # 直接显示服务端返回的错误信息
            if "ResponseMetadata" in result and "Error" in result["ResponseMetadata"]:
                error_info = result["ResponseMetadata"]["Error"]
                error_code = error_info.get("Code", "Unknown")
                error_msg = error_info.get("Message", "No message")
                print(f"\n服务端错误：")
                print(f"  Code: {error_code}")
                print(f"  Message: {error_msg}")
            sys.exit(1)
    except Exception as e:
        error(f"[Submit] Exception: {e}")
        # 尝试解析错误响应中的 JSON
        error_str = str(e)
        if error_str.startswith("b'") and "ResponseMetadata" in error_str:
            try:
                # 移除 b' 前缀和 ' 后缀，然后解析 JSON
                json_str = error_str[2:-1].replace("\\'", "'")
                error_data = json.loads(json_str)
                if "ResponseMetadata" in error_data and "Error" in error_data["ResponseMetadata"]:
                    error_info = error_data["ResponseMetadata"]["Error"]
                    error_code = error_info.get("Code", "Unknown")
                    error_msg = error_info.get("Message", "No message")
                    print(f"\n服务端错误：")
                    print(f"  Code: {error_code}")
                    print(f"  Message: {error_msg}")
            except:
                pass
        sys.exit(1)


def query_project_status(client: Service, space_name: str, project_id: str, project_version: str) -> dict:
    """
    查询翻译项目状态。
    
    Args:
        client: VOD 客户端
        space_name: VOD 空间名称
        project_id: 项目 ID
        project_version: 项目版本
    
    Returns:
        项目详细信息
    """
    body = {
        "SpaceName": space_name,
        "ProjectId": project_id,
        "ProjectVersion": project_version,
    }
    
    try:
        resp = client.json("GetAITranslationProject", {}, json.dumps(body))
        result = json.loads(resp)
        
        if "Result" in result:
            return result["Result"]
        else:
            error(f"[Query] Invalid response: {result}")
            return None
    except Exception as e:
        error(f"[Query] Exception: {e}")
        return None


def wait_for_completion(
    client: Service,
    space_name: str,
    project_id: str,
    project_version: str,
    max_wait_seconds: int = 3600,
) -> dict:
    """
    轮询等待翻译任务完成。
    
    Args:
        client: VOD 客户端
        space_name: VOD 空间名称
        project_id: 项目 ID
        project_version: 项目版本
        max_wait_seconds: 最大等待时间（秒，默认 3600）
    
    Returns:
        项目详细信息
    """
    start_time = time.time()
    poll_interval = 10  # 每 10 秒轮询一次
    
    info(f"[Wait] Polling for translation completion (ProjectId={project_id})...")
    
    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait_seconds:
            error(f"[Wait] Timeout after {max_wait_seconds} seconds")
            sys.exit(1)
        
        result = query_project_status(client, space_name, project_id, project_version)
        if result is None:
            info(f"[Wait] Query failed, retrying... (elapsed: {elapsed:.1f}s)")
            time.sleep(poll_interval)
            continue
        
        # 检查状态（根据实际 API 响应调整字段名）
        status = result.get("Status", "Unknown")
        info(f"[Wait] Status: {status} (elapsed: {elapsed:.1f}s)")
        
        # 根据实际 API 文档调整状态判断逻辑
        if status in ["Success", "Completed", "Finished"]:
            info(f"[Wait] Translation completed in {elapsed:.1f} seconds")
            return result
        elif status in ["Failed", "Error"]:
            error(f"[Wait] Translation failed: {result}")
            sys.exit(1)
        
        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(
        description="火山引擎 AI 视频翻译（声影智译）测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 完整翻译（字幕+声音+口型）
  python test/volcengine_ai_translation.py --vid v02399g10001xxxxxxxxxxxxxxxxxxxx --space-name my-space --source-lang zh --target-lang en
  
  # 只翻译字幕
  python test/volcengine_ai_translation.py --vid v02399g10001xxxxxxxxxxxxxxxxxxxx --space-name my-space --source-lang zh --target-lang en --types SubtitleTranslation
  
  # 只翻译声音和口型
  python test/volcengine_ai_translation.py --vid v02399g10001xxxxxxxxxxxxxxxxxxxx --space-name my-space --source-lang zh --target-lang en --types VoiceTranslation

关于 Vid（视频 ID）：
  Vid 是视频上传到火山引擎视频点播（VOD）后返回的唯一标识符。
  
  获取方式：
  1. 通过控制台上传视频后，在媒资管理页面查看 Vid
  2. 通过 API 上传视频后，从响应中获取 Vid
  3. 参考文档：https://www.volcengine.com/docs/4/10176
  
  格式示例：v02399g10001xxxxxxxxxxxxxxxxxxxx

环境变量：
  VOLC_ACCESS_KEY: 火山引擎 Access Key
  VOLC_SECRET_KEY: 火山引擎 Secret Key
        """,
    )
    
    parser.add_argument(
        "--vid",
        required=True,
        help="视频 ID（Vid）- 视频上传到火山引擎 VOD 后返回的唯一标识符",
    )
    
    parser.add_argument(
        "--space-name",
        required=True,
        help="VOD 空间名称（SpaceName）",
    )
    
    parser.add_argument(
        "--source-lang",
        default="zh",
        help="源语言（默认: zh）",
    )
    
    parser.add_argument(
        "--target-lang",
        default="en",
        help="目标语言（默认: en）",
    )
    
    parser.add_argument(
        "--types",
        nargs="+",
        choices=["SubtitleTranslation", "VoiceTranslation"],
        default=["SubtitleTranslation", "VoiceTranslation"],
        help="翻译类型（默认: SubtitleTranslation VoiceTranslation）",
    )
    
    parser.add_argument(
        "--region",
        default="cn-north-1",
        help="区域（默认: cn-north-1）",
    )
    
    parser.add_argument(
        "--max-wait",
        type=int,
        default=3600,
        help="最大等待时间（秒，默认: 3600）",
    )
    
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="不等待任务完成，只提交任务",
    )
    
    args = parser.parse_args()
    
    # 加载密钥
    ak, sk = load_keys()
    
    # 构建客户端
    client = build_vod_client(ak, sk, region=args.region)
    
    # 构建配置（命令行参数会覆盖脚本中的默认配置）
    # 如果命令行参数提供了值，就使用命令行参数；否则使用脚本中的默认配置
    process_config = None
    subtitle_config = None
    termbase_config = None
    
    # 检查是否有命令行参数需要构建配置
    has_process_config = hasattr(args, 'enable_lip_sync') and (args.enable_lip_sync or args.enable_voice_clone or getattr(args, 'background_volume', 100) != 100)
    has_subtitle_config = hasattr(args, 'subtitle_format') and args.subtitle_format
    has_termbase_config = hasattr(args, 'termbase_id') and args.termbase_id
    
    if has_process_config:
        # 构建 ProcessConfig
        process_config = {}
        if hasattr(args, 'enable_lip_sync') and args.enable_lip_sync:
            process_config["EnableLipSync"] = True
        if hasattr(args, 'enable_voice_clone') and args.enable_voice_clone:
            process_config["EnableVoiceClone"] = True
        if hasattr(args, 'background_volume') and args.background_volume != 100:
            process_config["BackgroundVolume"] = args.background_volume
    
    if has_subtitle_config:
        # 构建 SubtitleConfig
        subtitle_config = {
            "SubtitleFormat": args.subtitle_format,
        }
    
    if has_termbase_config:
        # 构建 TermbaseConfig
        termbase_config = {"TranslationTermbaseIds": [args.termbase_id]}
    
    # 提交任务（如果参数为 None，函数会自动使用脚本顶部的配置字典）
    project_info = submit_translation_task(
        client,
        space_name=args.space_name,
        vid=args.vid,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        translation_types=args.types,
        subtitle_config=subtitle_config,
        process_config=process_config,
        termbase_config=termbase_config,
    )
    
    project_id = project_info["ProjectId"]
    project_version = project_info["ProjectVersion"]
    
    info(f"[Done] Translation task submitted successfully")
    info(f"  ProjectId: {project_id}")
    info(f"  ProjectVersion: {project_version}")
    
    if args.no_wait:
        info(f"[Done] Task submitted, not waiting for completion")
        print(f"\n查询任务状态：")
        print(f"  python test/volcengine_ai_translation.py --query --space-name {args.space_name} --project-id {project_id} --project-version {project_version}")
    else:
        # 等待任务完成
        result = wait_for_completion(
            client,
            space_name=args.space_name,
            project_id=project_id,
            project_version=project_version,
            max_wait_seconds=args.max_wait,
        )
        
        info(f"[Done] Translation completed successfully")
        # 输出结果摘要（根据实际 API 响应调整）
        print(f"\n翻译结果：")
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
