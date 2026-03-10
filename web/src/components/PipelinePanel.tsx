/** PipelinePanel: clickable stage bar + smart action button + log viewer */
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

// ---- StageBar ----

function StageBar({
  stages, gates, selectedStage, onStageClick, onGateClick,
}: {
  stages: StageInfo[]
  gates: GateStatus[]
  selectedStage: string | null
  onStageClick: (stage: StageInfo) => void
  onGateClick: (gate: GateStatus) => void
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
        const isSelected = stage.key === selectedStage
        const isClickable = stage.status !== 'running'
        return (
          <div key={stage.key} className="flex items-center">
            {i > 0 && (
              <span className="text-gray-600 text-[10px] mx-0.5">{'\u2192'}</span>
            )}
            <span
              className={`
                px-2 py-0.5 rounded text-[10px] font-mono select-none
                ${isClickable ? 'cursor-pointer hover:brightness-125' : 'cursor-default'}
                ${stageChipStyles[stage.status]}
                ${isSelected ? 'ring-2 ring-yellow-400' : stageChipBorder[stage.status]}
              `}
              title={`${stage.label} [${stage.phases.join(', ')}] (${stage.status})`}
              onClick={() => isClickable && onStageClick(stage)}
            >
              {stage.label}
            </span>
            {/* Gate indicator after this stage — clickable when awaiting or passed */}
            {gate && (
              <>
                <span className="text-gray-600 text-[10px] mx-0.5">{'\u2192'}</span>
                <span
                  className={`
                    px-1.5 py-0.5 rounded text-[9px] font-mono
                    ${gateStyles[gate.status]}
                    ${gate.status !== 'pending' ? 'cursor-pointer hover:brightness-125' : ''}
                  `}
                  title={gate.status === 'awaiting' ? `${gate.label} — click to pass` : gate.status === 'passed' ? `${gate.label} — click to reset` : `${gate.label}`}
                  onClick={() => gate.status !== 'pending' && onGateClick(gate)}
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
        <span className="text-gray-600">暂无日志</span>
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

// ---- Derive smart button state ----

interface SmartAction {
  label: string
  style: 'blue' | 'orange' | 'red'
  action: 'execute' | 'rerun' | 'cancel'
  fromPhase?: string
}

function deriveSmartAction(
  selectedStage: string | null,
  stages: StageInfo[],
  currentAction: ReturnType<typeof usePipelineStore.getState>['currentAction'],
  isRunning: boolean,
): SmartAction | null {
  if (isRunning) {
    return { label: '取消', style: 'red', action: 'cancel' }
  }

  // User clicked a stage → rerun from that stage
  if (selectedStage) {
    const stage = stages.find(s => s.key === selectedStage)
    if (stage) {
      return {
        label: `重跑「${stage.label}」`,
        style: 'orange',
        action: 'rerun',
        fromPhase: stage.phases[0],
      }
    }
  }

  // No selection → default workflow action
  if (currentAction) {
    return { label: currentAction.label, style: 'blue', action: 'execute' }
  }

  return null
}

const buttonStyles: Record<string, string> = {
  blue:   'bg-blue-600 hover:bg-blue-500 text-white',
  orange: 'bg-orange-600 hover:bg-orange-500 text-white',
  red:    'bg-red-600 hover:bg-red-500 text-white',
}

// ---- Main Panel ----

export function PipelinePanel() {
  const currentDrama = useModelStore(s => s.currentDrama)
  const currentEpisode = useModelStore(s => s.currentEpisode)
  const dirty = useModelStore(s => s.dirty)
  const saveCues = useModelStore(s => s.saveCues)
  const loadCues = useModelStore(s => s.loadCues)
  const phases = usePipelineStore(s => s.phases)
  const gates = usePipelineStore(s => s.gates)
  const stages = usePipelineStore(s => s.stages)
  const currentAction = usePipelineStore(s => s.currentAction)
  const isRunning = usePipelineStore(s => s.isRunning)
  const logs = usePipelineStore(s => s.logs)
  const runError = usePipelineStore(s => s.runError)
  const runPipeline = usePipelineStore(s => s.runPipeline)
  const executeAction = usePipelineStore(s => s.executeAction)
  const cancelRun = usePipelineStore(s => s.cancelRun)

  const hasEpisode = !!(currentDrama && currentEpisode)

  // Selected stage for rerun targeting
  const [selectedStage, setSelectedStage] = useState<string | null>(null)

  // Clear selection when pipeline starts running or episode changes
  useEffect(() => {
    if (isRunning) setSelectedStage(null)
  }, [isRunning])
  useEffect(() => {
    setSelectedStage(null)
  }, [currentDrama, currentEpisode])

  // Track previous isRunning to detect completion
  const prevRunningRef = useRef(isRunning)
  useEffect(() => {
    if (prevRunningRef.current && !isRunning && currentDrama && currentEpisode) {
      // Reload SRC cues (text_en is filled by MT directly on SRC cues)
      loadCues(currentDrama, currentEpisode)
    }
    prevRunningRef.current = isRunning
  }, [isRunning, currentDrama, currentEpisode, loadCues])

  const smartAction = useMemo(
    () => deriveSmartAction(selectedStage, stages, currentAction, isRunning),
    [selectedStage, stages, currentAction, isRunning],
  )

  const handleStageClick = useCallback((stage: StageInfo) => {
    if (!hasEpisode || isRunning) return
    // Toggle: click same stage to deselect
    setSelectedStage(prev => prev === stage.key ? null : stage.key)
  }, [hasEpisode, isRunning])

  const resetGate = usePipelineStore(s => s.resetGate)

  const handleGateClick = useCallback(async (gate: GateStatus) => {
    if (!currentDrama || !currentEpisode || isRunning) return
    if (dirty) await saveCues()
    if (gate.status === 'awaiting') {
      const passGate = usePipelineStore.getState().passGate
      await passGate(currentDrama, currentEpisode, gate.key)
    } else if (gate.status === 'passed') {
      await resetGate(currentDrama, currentEpisode, gate.key)
    }
  }, [currentDrama, currentEpisode, isRunning, dirty, saveCues, resetGate])

  const handleButtonClick = useCallback(async () => {
    if (!smartAction || !currentDrama || !currentEpisode) return

    if (smartAction.action === 'cancel') {
      cancelRun()
      return
    }

    if (dirty) {
      await saveCues()
    }

    if (smartAction.action === 'rerun' && smartAction.fromPhase) {
      runPipeline(currentDrama, currentEpisode, smartAction.fromPhase)
    } else {
      executeAction(currentDrama, currentEpisode)
    }
    setSelectedStage(null)
  }, [smartAction, currentDrama, currentEpisode, dirty, saveCues, runPipeline, executeAction, cancelRun])

  // Find status indicators
  const awaitingGate = gates.find(g => g.status === 'awaiting')
  const runningPhase = phases.find(p => p.status === 'running')

  return (
    <div className="bg-gray-800">
      {/* Stage bar + action button */}
      <div className="flex items-center gap-3 px-3 py-1.5">
        <StageBar
          stages={stages}
          gates={gates}
          selectedStage={selectedStage}
          onStageClick={handleStageClick}
          onGateClick={handleGateClick}
        />

        <div className="flex-1" />

        {/* Status indicator */}
        {runningPhase && (
          <span className="text-[10px] text-blue-400 animate-pulse shrink-0">
            {runningPhase.label}...
          </span>
        )}
        {awaitingGate && !isRunning && !selectedStage && (
          <span className="text-[10px] text-yellow-400 shrink-0">
            {awaitingGate.label}
          </span>
        )}

        {/* Smart action button */}
        {smartAction && (
          <button
            onClick={handleButtonClick}
            disabled={!hasEpisode}
            className={`px-3 py-0.5 rounded text-xs shrink-0 disabled:opacity-40 disabled:cursor-not-allowed ${buttonStyles[smartAction.style]}`}
          >
            {smartAction.label}
          </button>
        )}
      </div>

      {/* Log viewer: show when there are logs */}
      {(logs.length > 0 || runError) && (
        <div className="px-3 pb-2 border-t border-gray-700">
          <LogViewer logs={logs} runError={runError} />
        </div>
      )}
    </div>
  )
}
