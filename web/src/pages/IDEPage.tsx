/** IDE Page — subtitle editor, extracted from former App.tsx */
import { useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useModelStore } from '../stores/model-store'
import { usePipelineStore } from '../stores/pipeline-store'
import { PlayerEngine } from '../components/PlayerEngine'
import { PlaybackControls } from '../components/PlaybackControls'
import { TimelineView } from '../components/TimelineView'
import { TranscriptList } from '../components/TranscriptList'

import { PipelinePanel } from '../components/PipelinePanel'
import { StatusBar } from '../components/StatusBar'
import { useKeyboard } from '../hooks/useKeyboard'

export function IDEPage() {
  const { episodeId } = useParams<{ episodeId: string }>()
  const navigate = useNavigate()

  const episodes = useModelStore(s => s.episodes)
  const currentDrama = useModelStore(s => s.currentDrama)
  const loading = useModelStore(s => s.loading)
  const error = useModelStore(s => s.error)
  const dirty = useModelStore(s => s.dirty)
  const loadEpisodes = useModelStore(s => s.loadEpisodes)
  const selectEpisode = useModelStore(s => s.selectEpisode)
  const saveCues = useModelStore(s => s.saveCues)
  const loadEmotions = useModelStore(s => s.loadEmotions)

  const pipelineIsRunning = usePipelineStore(s => s.isRunning)
  const pipelinePhases = usePipelineStore(s => s.phases)
  const pipelineGates = usePipelineStore(s => s.gates)

  useEffect(() => {
    loadEpisodes()
    loadEmotions()
  }, [loadEpisodes, loadEmotions])

  // Once episodes are loaded, find the one matching the URL and select it
  useEffect(() => {
    if (episodes.length === 0 || !episodeId) return
    const id = Number(episodeId)
    const ep = episodes.find(e => e.id === id)
    if (!ep) return
    // Only select if not already selected
    if (currentDrama === ep.drama && useModelStore.getState().currentEpisode === ep.episode) return
    selectEpisode(ep.drama, ep.episode)
  }, [episodes, episodeId, selectEpisode, currentDrama])

  useKeyboard()

  // Derive breadcrumb info from episodes list
  const id = Number(episodeId)
  const currentEp = episodes.find(e => e.id === id)
  const dramaName = currentEp?.drama ?? ''
  const dramaId = currentEp?.drama_id

  return (
    <div className="h-full flex flex-col bg-gray-900 text-gray-100">
      {/* Header */}
      <header className="flex items-center gap-3 px-4 py-0 h-11 bg-[#12141c] border-b border-white/[0.06] shrink-0">
        {/* Breadcrumb navigation */}
        {dramaId ? (
          <button
            onClick={() => navigate(`/drama/${dramaId}`)}
            className="text-xs text-gray-400 hover:text-gray-200 transition-colors truncate max-w-[160px]"
          >
            {dramaName}
          </button>
        ) : (
          <span className="text-xs text-gray-500">...</span>
        )}

        <svg className="w-3 h-3 text-gray-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
        </svg>

        <span className="text-xs font-medium text-gray-200">
          {currentEp ? `第 ${currentEp.episode} 集` : '...'}
        </span>

        <div className="flex-1" />

        {/* Workflow status */}
        {pipelineIsRunning && (() => {
          const runningPhase = pipelinePhases.find(p => p.status === 'running')
          return (
            <span className="text-[11px] text-blue-400 animate-pulse">
              {runningPhase?.label ?? '...'}
            </span>
          )
        })()}
        {!pipelineIsRunning && (() => {
          const awaitingGate = pipelineGates.find(g => g.status === 'awaiting')
          return awaitingGate ? (
            <span className="text-[11px] text-amber-400">
              {awaitingGate.label}
            </span>
          ) : null
        })()}
        {error && (
          <span className="text-[11px] text-red-400 truncate max-w-[200px]">{error}</span>
        )}

        {dramaId && (
          <Link
            to={`/casting/${dramaId}`}
            className="h-6 px-2.5 text-[11px] rounded-md bg-white/[0.04] border border-white/[0.06] hover:border-white/[0.12] hover:bg-white/[0.06] transition-all flex items-center"
          >
            音色分配
          </Link>
        )}

        <button
          onClick={saveCues}
          disabled={!dirty || loading}
          className="h-6 px-3 text-[11px] font-medium rounded-md bg-blue-600 hover:bg-blue-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          保存
        </button>
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
