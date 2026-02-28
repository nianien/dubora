/** ASR Calibration IDE - Main application layout */
import { useEffect, useState, useCallback } from 'react'
import { useModelStore } from './stores/model-store'
import { usePipelineStore } from './stores/pipeline-store'
import { PlayerEngine } from './components/PlayerEngine'
import { PlaybackControls } from './components/PlaybackControls'
import { TimelineView } from './components/TimelineView'
import { TranscriptList } from './components/TranscriptList'
import { ToolBar } from './components/ToolBar'
import { PipelinePanel } from './components/PipelinePanel'
import { StatusBar } from './components/StatusBar'
import { useKeyboard } from './hooks/useKeyboard'
import type { Episode } from './types/asr-model'

export default function App() {
  const {
    episodes, currentDrama, currentEpisode,
    loading, error, dirty,
    loadEpisodes, selectEpisode, saveModel,
    loadEmotions,
  } = useModelStore()

  const pipelineIsRunning = usePipelineStore(s => s.isRunning)
  const pipelineSteps = usePipelineStore(s => s.steps)

  useEffect(() => {
    loadEpisodes()
    loadEmotions()
  }, [loadEpisodes, loadEmotions])

  useKeyboard()

  const [selectedDrama, setSelectedDrama] = useState<string>('')

  const handleDramaChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedDrama(e.target.value)
  }, [])

  const handleEpisodeChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    const ep = e.target.value
    if (!ep || !selectedDrama) return
    selectEpisode(selectedDrama, ep)
  }, [selectedDrama, selectEpisode])

  // Sync selectedDrama when currentDrama changes (e.g. initial load)
  useEffect(() => {
    if (currentDrama && !selectedDrama) setSelectedDrama(currentDrama)
  }, [currentDrama, selectedDrama])

  // Group episodes by drama
  const dramaGroups: Record<string, Episode[]> = {}
  for (const ep of episodes) {
    if (!dramaGroups[ep.drama]) dramaGroups[ep.drama] = []
    dramaGroups[ep.drama].push(ep)
  }

  return (
    <div className="h-screen flex flex-col bg-gray-900 text-gray-100">
      {/* Header */}
      <header className="flex items-center gap-4 px-4 py-2 bg-gray-800 border-b border-gray-700 shrink-0">
        <h1 className="text-sm font-bold text-gray-300">ASR IDE</h1>

        <select
          value={selectedDrama}
          onChange={handleDramaChange}
          className="bg-gray-700 text-gray-200 text-sm rounded px-2 py-1 outline-none"
        >
          <option value="">Select drama...</option>
          {Object.keys(dramaGroups).map(drama => (
            <option key={drama} value={drama}>{drama} ({dramaGroups[drama].length})</option>
          ))}
        </select>

        <select
          value={currentDrama === selectedDrama ? currentEpisode : ''}
          onChange={handleEpisodeChange}
          disabled={!selectedDrama}
          className="bg-gray-700 text-gray-200 text-sm rounded px-2 py-1 outline-none disabled:opacity-40"
        >
          <option value="">Select episode...</option>
          {(dramaGroups[selectedDrama] ?? []).map(ep => (
            <option key={ep.episode} value={ep.episode}>
              Ep {ep.episode}
            </option>
          ))}
        </select>

        <div className="flex-1" />

        <button
          onClick={saveModel}
          disabled={!dirty || loading}
          className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Save
        </button>

        {/* Workflow status indicator */}
        {pipelineIsRunning && (() => {
          const runningStep = pipelineSteps.find(s => s.status === 'running')
          return (
            <span className="text-xs text-blue-400 animate-pulse">
              {runningStep?.label ?? '...'}
            </span>
          )
        })()}
        {!pipelineIsRunning && (() => {
          const calibStep = pipelineSteps.find(s => s.status === 'calibrating')
          return calibStep ? (
            <span className="text-xs text-yellow-400">
              {calibStep.label}
            </span>
          ) : null
        })()}
        {error && (
          <span className="text-xs text-red-400">{error}</span>
        )}
      </header>

      {/* Main content: transcript left, video+toolbar right */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Transcript list */}
        <div className="w-1/2 flex flex-col border-r border-gray-700">
          <div className="flex-1 min-h-0">
            <TranscriptList />
          </div>
        </div>

        {/* Right: Video + ToolBar + PipelinePanel */}
        <div className="w-1/2 flex flex-col">
          <div className="flex-1 min-h-0">
            <PlayerEngine />
          </div>
          <div className="shrink-0 border-t border-gray-700">
            <ToolBar />
          </div>
          <div className="shrink-0 border-t border-gray-700">
            <PipelinePanel />
          </div>
        </div>
      </div>

      {/* Bottom: Playback controls + Timeline */}
      <div className="shrink-0">
        <PlaybackControls />
        <TimelineView />
      </div>

      {/* Status bar */}
      <StatusBar />
    </div>
  )
}
