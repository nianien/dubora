/** Video player with native timeupdate sync + subtitle overlay */
import { useRef, useEffect, useMemo } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'

export function PlayerEngine() {
  const videoRef = useRef<HTMLVideoElement>(null)
  const seekingExternalRef = useRef(false)

  const videoFile = useModelStore(s => s.videoFile)
  const model = useModelStore(s => s.model)
  const currentTime = useEditorStore(s => s.currentTime)
  const segments = model?.segments ?? []

  // Find the segment that covers the current playback time
  const currentSub = useMemo(() => {
    return segments.find(s => s.start_ms <= currentTime && currentTime < s.end_ms) ?? null
  }, [segments, currentTime])

  const videoSrc = videoFile ? `/api/media/${videoFile}` : null

  // Bind all video events — use getState() to avoid subscribing to store changes
  useEffect(() => {
    const v = videoRef.current
    if (!v) return

    const set = useEditorStore.setState

    const onTimeUpdate = () => {
      if (!seekingExternalRef.current) {
        set({ currentTime: Math.round(v.currentTime * 1000) })
      }
    }
    const onPlay = () => {
      set({ isPlaying: true })
    }
    const onPause = () => {
      set({ isPlaying: false, currentTime: Math.round(v.currentTime * 1000) })
    }
    const onSeeked = () => {
      seekingExternalRef.current = false
      set({ currentTime: Math.round(v.currentTime * 1000) })
    }
    const onLoadedMetadata = () => {
      if (v.duration && isFinite(v.duration)) {
        set({ duration: Math.round(v.duration * 1000) })
      }
    }

    v.addEventListener('timeupdate', onTimeUpdate)
    v.addEventListener('play', onPlay)
    v.addEventListener('pause', onPause)
    v.addEventListener('seeked', onSeeked)
    v.addEventListener('loadedmetadata', onLoadedMetadata)
    v.addEventListener('durationchange', onLoadedMetadata)
    return () => {
      v.removeEventListener('timeupdate', onTimeUpdate)
      v.removeEventListener('play', onPlay)
      v.removeEventListener('pause', onPause)
      v.removeEventListener('seeked', onSeeked)
      v.removeEventListener('loadedmetadata', onLoadedMetadata)
      v.removeEventListener('durationchange', onLoadedMetadata)
    }
  }, [videoSrc])

  // External seek: when currentTime changes from outside (click subtitle / timeline)
  useEffect(() => {
    const unsub = useEditorStore.subscribe(
      (state, prev) => {
        if (state.currentTime === prev.currentTime) return
        const v = videoRef.current
        if (!v) return
        const videoMs = Math.round(v.currentTime * 1000)
        const diff = Math.abs(videoMs - state.currentTime)
        // Only seek if the store time jumped significantly vs actual video position
        if (diff > 300) {
          seekingExternalRef.current = true
          v.currentTime = state.currentTime / 1000
        }
      }
    )
    return unsub
  }, [])

  if (!videoSrc) {
    return (
      <div className="flex items-center justify-center h-full bg-gray-900 text-gray-500">
        {model ? 'Video file not found' : 'No video loaded'}
      </div>
    )
  }

  return (
    <div className="relative h-full bg-black flex items-center justify-center">
      <video
        ref={videoRef}
        src={videoSrc}
        className="max-w-full max-h-full cursor-pointer"
        onClick={() => {
          const v = videoRef.current
          if (v) v.paused ? v.play() : v.pause()
        }}
        preload="auto"
      />
      {currentSub && (
        <div className="absolute bottom-4 left-0 right-0 flex justify-center pointer-events-none px-4">
          <span
            className="text-white px-3 py-1 rounded max-w-[90%] text-center"
            style={{ background: 'rgba(0,0,0,0.7)' }}
          >
            <div className="text-lg leading-snug">{currentSub.text}</div>
            {currentSub.text_en && (
              <div className="text-sm text-gray-300 leading-snug">{currentSub.text_en}</div>
            )}
          </span>
        </div>
      )}
    </div>
  )
}
