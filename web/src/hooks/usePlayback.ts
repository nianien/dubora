/** Playback state synchronization hook */
import { useCallback } from 'react'
import { useEditorStore } from '../stores/editor-store'

/**
 * Hook to get a seekTo function that syncs video + store.
 */
export function usePlayback() {
  const { setCurrentTime } = useEditorStore()

  const seekTo = useCallback((ms: number) => {
    const video = document.querySelector('video')
    if (video) {
      video.currentTime = ms / 1000
    }
    setCurrentTime(ms)
  }, [setCurrentTime])

  const play = useCallback(() => {
    const video = document.querySelector('video')
    if (video) video.play()
  }, [])

  const pause = useCallback(() => {
    const video = document.querySelector('video')
    if (video) video.pause()
  }, [])

  const togglePlay = useCallback(() => {
    const video = document.querySelector('video')
    if (!video) return
    if (video.paused) video.play()
    else video.pause()
  }, [])

  return { seekTo, play, pause, togglePlay }
}
