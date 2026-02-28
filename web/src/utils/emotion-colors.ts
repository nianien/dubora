/** Emotion → hex color mapping for inline styles (avoids Tailwind purge issues) */

export const EMOTION_COLORS: Record<string, { bg: string; text: string }> = {
  neutral:   { bg: '#4b5563', text: '#9ca3af' },  // gray
  happy:     { bg: '#b45309', text: '#fbbf24' },  // amber
  sad:       { bg: '#1e40af', text: '#60a5fa' },  // blue
  angry:     { bg: '#b91c1c', text: '#f87171' },  // red
  fearful:   { bg: '#7e22ce', text: '#c084fc' },  // purple
  surprised: { bg: '#0e7490', text: '#22d3ee' },  // cyan
}

export function emotionColor(emotion: string) {
  return EMOTION_COLORS[emotion] ?? EMOTION_COLORS.neutral
}
