/** Playback control bar: play/pause, seek bar, time display */
import { useCallback, useRef } from 'react'
import { useEditorStore } from '../stores/editor-store'
import { msToDisplay } from '../utils/time'

export function PlaybackControls() {
  const { currentTime, duration, isPlaying, setCurrentTime } = useEditorStore()
  const seekBarRef = useRef<HTMLDivElement>(null)

  const togglePlay = useCallback(() => {
    const video = document.querySelector('video')
    if (!video) return
    if (video.paused) video.play()
    else video.pause()
  }, [])

  const handleSeek = useCallback((e: React.MouseEvent) => {
    const bar = seekBarRef.current
    if (!bar || !duration) return
    const rect = bar.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    const ms = Math.round(ratio * duration)
    setCurrentTime(ms)
    const video = document.querySelector('video')
    if (video) video.currentTime = ms / 1000
  }, [duration, setCurrentTime])

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0

  return (
    <div className="flex items-center gap-3 px-3 py-1.5 bg-gray-800 border-t border-gray-700">
      {/* Play/Pause */}
      <button
        onClick={togglePlay}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-gray-700 hover:bg-gray-600 text-white shrink-0"
      >
        {isPlaying ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
            <rect x="2" y="1" width="3.5" height="12" rx="0.5" />
            <rect x="8.5" y="1" width="3.5" height="12" rx="0.5" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
            <path d="M3 1.5v11l9-5.5z" />
          </svg>
        )}
      </button>

      {/* Time display */}
      <span className="text-xs font-mono text-gray-300 w-28 shrink-0">
        {msToDisplay(currentTime)} / {msToDisplay(duration)}
      </span>

      {/* Seek bar */}
      <div
        ref={seekBarRef}
        className="flex-1 h-1.5 bg-gray-600 rounded-full cursor-pointer relative group"
        onClick={handleSeek}
      >
        {/* Progress fill */}
        <div
          className="absolute inset-y-0 left-0 bg-blue-500 rounded-full"
          style={{ width: `${progress}%` }}
        />
        {/* Thumb */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity shadow"
          style={{ left: `calc(${progress}% - 6px)` }}
        />
      </div>
    </div>
  )
}
