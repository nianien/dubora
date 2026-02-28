/** Toolbar — selected segment info */
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'

export function ToolBar() {
  const model = useModelStore(s => s.model)
  const { selectedSegmentId } = useEditorStore()

  if (!model) return null

  const selectedSeg = model.segments.find(s => s.id === selectedSegmentId)

  return (
    <div className="flex items-center gap-4 px-3 py-2 text-xs overflow-x-auto">
      {selectedSeg ? (
        <>
          <span className="text-gray-500 shrink-0">
            {(selectedSeg.start_ms / 1000).toFixed(1)}s - {(selectedSeg.end_ms / 1000).toFixed(1)}s
            ({((selectedSeg.end_ms - selectedSeg.start_ms) / 1000).toFixed(1)}s)
          </span>
          {selectedSeg.flags.overlap && <span className="text-red-400 shrink-0">Overlap</span>}
        </>
      ) : (
        <span className="text-gray-500">No segment selected</span>
      )}
    </div>
  )
}
