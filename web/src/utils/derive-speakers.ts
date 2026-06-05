import type { Cue } from '../types/asr-model'

/** 按 role_id 推导出现顺序去重的列表（null 视为同一组）。
 * 命名保留 deriveSpeakers 是历史原因——新模型下决定音色身份的是 role_id。
 */
export function deriveSpeakers(cues: Cue[]): (number | null)[] {
  const seen = new Set<number | null>()
  const result: (number | null)[] = []
  for (const cue of cues) {
    const id = cue.role_id ?? null
    if (!seen.has(id)) {
      seen.add(id)
      result.push(id)
    }
  }
  return result
}
