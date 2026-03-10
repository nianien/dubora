/** Toolbar — selected cue info */
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'

export function ToolBar() {
  const cues = useModelStore(s => s.cues)
  const loaded = useModelStore(s => s.loaded)
  const selectedCueId = useEditorStore(s => s.selectedCueId)

  if (!loaded) return null

  const selectedCue = cues.find(c => c.id === selectedCueId)

  return (
    <div className="flex items-center gap-4 px-3 py-2 text-xs overflow-x-auto">
      {selectedCue ? (
        <span className="text-gray-500 shrink-0">
          {(selectedCue.start_ms / 1000).toFixed(1)}s - {(selectedCue.end_ms / 1000).toFixed(1)}s
          ({((selectedCue.end_ms - selectedCue.start_ms) / 1000).toFixed(1)}s)
        </span>
      ) : (
        <span className="text-gray-500">未选择字幕</span>
      )}
    </div>
  )
}
