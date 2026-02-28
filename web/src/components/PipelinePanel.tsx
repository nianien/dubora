/** PipelinePanel: stage-based workflow with StageBar + Gate indicators + ActionButton + LogViewer */
import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useModelStore } from '../stores/model-store'
import { usePipelineStore } from '../stores/pipeline-store'
import type { GateStatus, StageInfo } from '../types/pipeline'

// ---- Stage chip styles ----

const stageChipStyles: Record<StageInfo['status'], string> = {
  pending:   'bg-gray-700 text-gray-500',
  running:   'bg-blue-600 text-white animate-pulse',
  succeeded: 'bg-green-800 text-green-300',
  failed:    'bg-red-800 text-red-300',
}

const stageChipBorder: Record<StageInfo['status'], string> = {
  pending:   'ring-0',
  running:   'ring-2 ring-blue-400',
  succeeded: 'ring-0',
  failed:    'ring-2 ring-red-400',
}

// ---- Gate indicator styles ----

const gateStyles: Record<GateStatus['status'], string> = {
  pending:  'bg-gray-600 text-gray-500',
  awaiting: 'bg-yellow-700 text-yellow-200 ring-2 ring-yellow-400',
  passed:   'bg-green-900 text-green-400',
}

// Gate sits between stages: after "recognize" and after "translate"
const GATE_AFTER_STAGE: Record<string, string> = {
  source_review: 'recognize',
  translation_review: 'translate',
}

// ---- Context Menu ----

interface ContextMenuState {
  x: number
  y: number
  stage: StageInfo
}

function ContextMenu({
  menu, onRerun, onClose,
}: {
  menu: ContextMenuState
  onRerun: (fromPhase: string) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const firstPhase = menu.stage.phases[0]

  return (
    <div
      ref={ref}
      className="fixed z-50 bg-gray-700 border border-gray-600 rounded shadow-lg py-1 min-w-[120px]"
      style={{ left: menu.x, top: menu.y }}
    >
      <button
        className="w-full text-left px-3 py-1 text-xs text-gray-200 hover:bg-gray-600"
        onClick={() => { onRerun(firstPhase); onClose() }}
      >
        从「{menu.stage.label}」重跑
      </button>
    </div>
  )
}

// ---- StageBar ----

function StageBar({
  stages, gates, onContextMenu,
}: {
  stages: StageInfo[]
  gates: GateStatus[]
  onContextMenu: (e: React.MouseEvent, stage: StageInfo) => void
}) {
  const gateAfterStage = useMemo(() => {
    const map = new Map<string, GateStatus>()
    for (const g of gates) {
      const stageKey = GATE_AFTER_STAGE[g.key]
      if (stageKey) map.set(stageKey, g)
    }
    return map
  }, [gates])

  return (
    <div className="flex items-center gap-0.5 flex-wrap">
      {stages.map((stage, i) => {
        const gate = gateAfterStage.get(stage.key)
        return (
          <div key={stage.key} className="flex items-center">
            {i > 0 && (
              <span className="text-gray-600 text-[10px] mx-0.5">{'\u2192'}</span>
            )}
            <span
              className={`
                px-2 py-0.5 rounded text-[10px] font-mono cursor-context-menu select-none
                ${stageChipStyles[stage.status]}
                ${stageChipBorder[stage.status]}
              `}
              title={`${stage.label} [${stage.phases.join(', ')}] (${stage.status})`}
              onContextMenu={(e) => { e.preventDefault(); onContextMenu(e, stage) }}
            >
              {stage.label}
            </span>
            {/* Gate indicator after this stage */}
            {gate && (
              <>
                <span className="text-gray-600 text-[10px] mx-0.5">{'\u2192'}</span>
                <span
                  className={`
                    px-1.5 py-0.5 rounded text-[9px] font-mono
                    ${gateStyles[gate.status]}
                  `}
                  title={`${gate.label} (${gate.status})`}
                >
                  {gate.label}
                </span>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---- LogViewer ----

function LogViewer({ logs, runError }: { logs: string[]; runError: string | null }) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs.length])

  return (
    <div className="bg-gray-950 rounded text-[11px] font-mono p-2 overflow-y-auto max-h-40 min-h-[60px]">
      {logs.length === 0 && !runError && (
        <span className="text-gray-600">No logs yet</span>
      )}
      {logs.map((line, i) => (
        <div key={i} className="text-gray-400 leading-tight whitespace-pre-wrap break-all">
          {line}
        </div>
      ))}
      {runError && (
        <div className="text-red-400 leading-tight">{runError}</div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}

// ---- Main Panel ----

export function PipelinePanel() {
  const { currentDrama, currentEpisode, dirty, saveModel, loadModel } = useModelStore()
  const {
    phases, gates, stages, currentAction, isRunning,
    logs, runError,
    runPipeline, cancelRun,
  } = usePipelineStore()

  const hasEpisode = !!(currentDrama && currentEpisode)

  // Context menu state
  const [ctxMenu, setCtxMenu] = useState<ContextMenuState | null>(null)

  // Track previous isRunning to detect completion
  const prevRunningRef = useRef(isRunning)
  useEffect(() => {
    if (prevRunningRef.current && !isRunning && currentDrama && currentEpisode) {
      // Pipeline just finished - reload model to pick up new data
      loadModel(currentDrama, currentEpisode)
    }
    prevRunningRef.current = isRunning
  }, [isRunning, currentDrama, currentEpisode, loadModel])

  const handleAction = useCallback(async () => {
    if (!currentDrama || !currentEpisode || !currentAction) return

    // Auto-save if dirty
    if (dirty) {
      await saveModel()
    }

    runPipeline(currentDrama, currentEpisode)
  }, [currentDrama, currentEpisode, dirty, saveModel, runPipeline, currentAction])

  const handleRerun = useCallback(async (fromPhase: string) => {
    if (!currentDrama || !currentEpisode || isRunning) return

    if (dirty) {
      await saveModel()
    }

    runPipeline(currentDrama, currentEpisode, fromPhase)
  }, [currentDrama, currentEpisode, isRunning, dirty, saveModel, runPipeline])

  const handleCancel = useCallback(() => {
    cancelRun()
  }, [cancelRun])

  const handleStageContextMenu = useCallback((e: React.MouseEvent, stage: StageInfo) => {
    if (!hasEpisode || isRunning) return
    setCtxMenu({ x: e.clientX, y: e.clientY, stage })
  }, [hasEpisode, isRunning])

  // Find status indicators
  const awaitingGate = gates.find(g => g.status === 'awaiting')
  const runningPhase = phases.find(p => p.status === 'running')

  return (
    <div className="bg-gray-800">
      {/* Stage bar + action button */}
      <div className="flex items-center gap-3 px-3 py-1.5">
        <StageBar stages={stages} gates={gates} onContextMenu={handleStageContextMenu} />

        <div className="flex-1" />

        {/* Status indicator */}
        {runningPhase && (
          <span className="text-[10px] text-blue-400 animate-pulse shrink-0">
            {runningPhase.label}...
          </span>
        )}
        {awaitingGate && !isRunning && (
          <span className="text-[10px] text-yellow-400 shrink-0">
            {awaitingGate.label}
          </span>
        )}

        {/* Action button */}
        {isRunning ? (
          <button
            onClick={handleCancel}
            className="px-3 py-0.5 rounded bg-red-600 hover:bg-red-500 text-white text-xs shrink-0"
          >
            Cancel
          </button>
        ) : currentAction ? (
          <button
            onClick={handleAction}
            disabled={!hasEpisode}
            className="px-3 py-0.5 rounded bg-blue-600 hover:bg-blue-500 text-white text-xs disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
          >
            {currentAction.label}
          </button>
        ) : null}
      </div>

      {/* Log viewer: show when there are logs */}
      {(logs.length > 0 || runError) && (
        <div className="px-3 pb-2 border-t border-gray-700">
          <LogViewer logs={logs} runError={runError} />
        </div>
      )}

      {/* Context menu */}
      {ctxMenu && (
        <ContextMenu
          menu={ctxMenu}
          onRerun={handleRerun}
          onClose={() => setCtxMenu(null)}
        />
      )}
    </div>
  )
}
