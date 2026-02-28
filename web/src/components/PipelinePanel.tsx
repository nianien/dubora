/** PipelinePanel: wizard-style 7-step workflow with StepBar + ActionButton + LogViewer */
import { useRef, useEffect, useCallback } from 'react'
import { useModelStore } from '../stores/model-store'
import { usePipelineStore } from '../stores/pipeline-store'
import type { WorkflowStep, StepStatus } from '../types/pipeline'

// ---- Step chip styles ----

const stepChipStyles: Record<StepStatus, string> = {
  pending:      'bg-gray-700 text-gray-500',
  running:      'bg-blue-600 text-white animate-pulse',
  done:         'bg-green-800 text-green-300',
  failed:       'bg-red-800 text-red-300',
  calibrating:  'bg-yellow-700 text-yellow-200',
}

const stepChipBorder: Record<StepStatus, string> = {
  pending:      'ring-0',
  running:      'ring-2 ring-blue-400',
  done:         'ring-0',
  failed:       'ring-2 ring-red-400',
  calibrating:  'ring-2 ring-yellow-400',
}

// ---- StepBar ----

function StepBar({
  steps,
  selectedStepKey,
  onClickStep,
}: {
  steps: WorkflowStep[]
  selectedStepKey: string | null
  onClickStep: (stepKey: string) => void
}) {
  return (
    <div className="flex items-center gap-0.5 flex-wrap">
      {steps.map((step, i) => {
        const clickable = step.status === 'done'
        const selected = step.key === selectedStepKey
        return (
          <div key={step.key} className="flex items-center">
            {i > 0 && (
              <span className="text-gray-600 text-[10px] mx-0.5">{'\u2192'}</span>
            )}
            <button
              onClick={() => clickable && onClickStep(step.key)}
              disabled={!clickable}
              className={`
                px-2 py-0.5 rounded text-[10px] font-mono
                ${stepChipStyles[step.status]}
                ${selected ? 'ring-2 ring-yellow-400' : stepChipBorder[step.status]}
                ${clickable ? 'cursor-pointer hover:ring-2 hover:ring-yellow-400' : ''}
                disabled:cursor-default
                transition-all
              `}
              title={
                clickable
                  ? `${step.label} (${step.status}) - click to re-run`
                  : `${step.label} (${step.status})`
              }
            >
              {step.label}
            </button>
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
    steps, currentAction, isRunning,
    logs, runError, selectedStepKey,
    runPipeline, cancelRun, selectStep,
  } = usePipelineStore()

  const hasEpisode = !!(currentDrama && currentEpisode)

  // Track previous isRunning to detect completion
  const prevRunningRef = useRef(isRunning)
  useEffect(() => {
    if (prevRunningRef.current && !isRunning && currentDrama && currentEpisode) {
      // Pipeline just finished - reload model to pick up new data (e.g. text_en after merge)
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

    runPipeline(currentDrama, currentEpisode, {
      from_phase: currentAction.fromPhase,
      to_phase: currentAction.toPhase,
    })
  }, [currentDrama, currentEpisode, dirty, saveModel, runPipeline, currentAction])

  const handleCancel = useCallback(() => {
    cancelRun()
  }, [cancelRun])

  const handleSelectStep = useCallback((stepKey: string) => {
    selectStep(stepKey)
  }, [selectStep])

  // Find current status text for header
  const calibratingStep = steps.find(s => s.status === 'calibrating')
  const runningStep = steps.find(s => s.status === 'running')

  return (
    <div className="bg-gray-800">
      {/* Step bar + action button */}
      <div className="flex items-center gap-3 px-3 py-1.5">
        <StepBar steps={steps} selectedStepKey={selectedStepKey} onClickStep={handleSelectStep} />

        <div className="flex-1" />

        {/* Status indicator */}
        {runningStep && (
          <span className="text-[10px] text-blue-400 animate-pulse shrink-0">
            {runningStep.label}...
          </span>
        )}
        {calibratingStep && !isRunning && (
          <span className="text-[10px] text-yellow-400 shrink-0">
            {calibratingStep.label}
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
    </div>
  )
}
