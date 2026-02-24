#!/usr/bin/env python3
"""
测试脚本：从 SSOT（Subtitle Model）中识别人名

用法：
    python test/name_guard_ssot.py <subtitle.model.json路径>

示例：
    python test/name_guard_ssot.py videos/dbqsfy/1/dub/1/subs/subtitle.model.json
"""
import sys
import json
from pathlib import Path
from typing import Dict, List, Tuple

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dubora.pipeline.processors.mt.name_guard import NameGuard, load_config


def load_subtitle_model(model_path: Path) -> Dict:
    """加载 Subtitle Model（SSOT）"""
    if not model_path.exists():
        raise FileNotFoundError(f"Subtitle Model 文件不存在: {model_path}")
    
    with open(model_path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_cue_texts(model: Dict) -> List[Tuple[str, str, str]]:
    """
    从 Subtitle Model 中提取所有 cue 的文本。
    
    Returns:
        List of (utterance_id, cue_id, text) tuples
    """
    cues = []
    
    # 遍历所有 utterances
    for utterance in model.get("utterances", []):
        utt_id = utterance.get("utterance_id", "")
        
        # 遍历 utterance 中的所有 cues
        for cue in utterance.get("cues", []):
            cue_id = cue.get("cue_id", "")
            source = cue.get("source", {})
            text = source.get("text", "").strip()
            
            if text:
                cues.append((utt_id, cue_id, text))
    
    return cues


def identify_names_in_ssot(model_path: Path, name_guard: NameGuard) -> Dict:
    """
    从 SSOT 中识别所有人名。
    
    Returns:
        {
            "total_utterances": int,
            "total_cues": int,
            "utterances_with_names": int,
            "cues_with_names": int,
            "all_names": Dict[str, int],  # {name: count}
            "details": List[Dict],  # 详细信息
        }
    """
    # 加载 Subtitle Model
    model = load_subtitle_model(model_path)
    
    # 提取所有 cue 文本
    cue_texts = extract_cue_texts(model)
    
    # 统计信息
    all_names = {}  # {name: count}
    details = []
    utterances_with_names = set()
    cues_with_names = 0
    
    # 处理每个 cue
    for utt_id, cue_id, text in cue_texts:
        # 使用 NameGuard 识别人名
        replaced_text, name_map = name_guard.extract_and_replace_names(text)
        
        if name_map:
            cues_with_names += 1
            utterances_with_names.add(utt_id)
            
            # 统计人名
            for placeholder, name in name_map.items():
                all_names[name] = all_names.get(name, 0) + 1
            
            details.append({
                "utterance_id": utt_id,
                "cue_id": cue_id,
                "original_text": text,
                "replaced_text": replaced_text,
                "names": list(name_map.values()),
                "name_map": name_map,
            })
    
    return {
        "total_utterances": len(set(utt_id for utt_id, _, _ in cue_texts)),
        "total_cues": len(cue_texts),
        "utterances_with_names": len(utterances_with_names),
        "cues_with_names": cues_with_names,
        "all_names": all_names,
        "details": details,
    }


def print_results(results: Dict, verbose: bool = False):
    """打印识别结果"""
    print("=" * 80)
    print("Name Guard 识别结果（SSOT）")
    print("=" * 80)
    print()
    
    # 统计信息
    print("📊 统计信息：")
    print(f"  总 utterances: {results['total_utterances']}")
    print(f"  总 cues: {results['total_cues']}")
    print(f"  包含人名的 utterances: {results['utterances_with_names']}")
    print(f"  包含人名的 cues: {results['cues_with_names']}")
    print()
    
    # 人名列表
    all_names = results['all_names']
    if all_names:
        print("👤 识别到的人名（按出现次数排序）：")
        sorted_names = sorted(all_names.items(), key=lambda x: x[1], reverse=True)
        for name, count in sorted_names:
            print(f"  {name}: {count} 次")
        print()
    else:
        print("👤 未识别到任何人名")
        print()
    
    # 详细信息（verbose 模式）
    if verbose and results['details']:
        print("📝 详细信息：")
        print("-" * 80)
        for detail in results['details']:
            print(f"Utterance: {detail['utterance_id']}, Cue: {detail['cue_id']}")
            print(f"  原文: {detail['original_text']}")
            print(f"  替换后: {detail['replaced_text']}")
            print(f"  识别到的人名: {', '.join(detail['names'])}")
            print()
    elif results['details']:
        print(f"💡 提示：使用 --verbose 查看详细信息（共 {len(results['details'])} 条）")
        print()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="从 SSOT（Subtitle Model）中识别人名",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 识别指定文件
  python test/name_guard_ssot.py videos/dbqsfy/1/dub/1/subs/subtitle.model.json
  
  # 详细模式
  python test/name_guard_ssot.py videos/dbqsfy/1/dub/1/subs/subtitle.model.json --verbose
        """
    )
    
    parser.add_argument(
        "model_path",
        type=Path,
        help="Subtitle Model 文件路径（subtitle.model.json）"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细信息（每个 cue 的识别结果）"
    )
    
    parser.add_argument(
        "--config",
        type=Path,
        help="Name Guard 配置文件路径（可选，默认使用内置配置）"
    )
    
    args = parser.parse_args()
    
    # 加载 Name Guard 配置
    if args.config:
        config = load_config(args.config)
    else:
        config = load_config()
    
    name_guard = NameGuard(config)
    
    # 识别人名
    try:
        results = identify_names_in_ssot(args.model_path, name_guard)
        print_results(results, verbose=args.verbose)
    except FileNotFoundError as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
