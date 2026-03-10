/** Status bar: dirty indicator, cue count, speakers */
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import { msToDisplay } from '../utils/time'
import { deriveSpeakers } from '../utils/derive-speakers'

export function StatusBar() {
  const cues = useModelStore(s => s.cues)
  const loaded = useModelStore(s => s.loaded)
  const dirty = useModelStore(s => s.dirty)
  const loading = useModelStore(s => s.loading)
  const currentTime = useEditorStore(s => s.currentTime)

  if (!loaded) {
    return (
      <div className="flex items-center justify-between px-3 py-1 bg-gray-800 border-t border-gray-700 text-xs text-gray-500">
        <span>未加载数据</span>
      </div>
    )
  }

  return (
    <div className="flex items-center justify-between px-3 py-1 bg-gray-800 border-t border-gray-700 text-xs text-gray-400">
      <div className="flex items-center gap-4">
        <span>{cues.length} 条字幕</span>
        <span>{deriveSpeakers(cues).length} 个角色</span>
        <span>{msToDisplay(currentTime)}</span>
      </div>
      <div className="flex items-center gap-4">
        {loading && <span className="text-blue-400">保存中...</span>}
        {dirty && !loading && <span className="text-yellow-400">有未保存的更改</span>}
        {!dirty && !loading && <span className="text-green-400">已保存</span>}
      </div>
    </div>
  )
}
