import type { AsrSegment } from '../types/asr-model'

export function deriveSpeakers(segments: AsrSegment[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const seg of segments) {
    if (!seen.has(seg.speaker)) {
      seen.add(seg.speaker)
      result.push(seg.speaker)
    }
  }
  return result
}
