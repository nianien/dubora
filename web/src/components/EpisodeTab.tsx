import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Drama, Episode } from '../types/asr-model'
import { postJson } from '../utils/api'

interface UploadItem {
  file: File
  episode: string
  progress: number      // 0-100
  status: 'pending' | 'uploading' | 'done' | 'error'
  error?: string
}

const STATUS_META: Record<string, { color: string; bg: string; label: string }> = {
  ready:     { color: 'text-gray-400',   bg: 'bg-gray-500/20',   label: '就绪' },
  running:   { color: 'text-blue-400',   bg: 'bg-blue-500/20',   label: '进行中' },
  succeeded: { color: 'text-green-400',  bg: 'bg-green-500/20',  label: '完成' },
  failed:    { color: 'text-red-400',    bg: 'bg-red-500/20',    label: '失败' },
  review:    { color: 'text-amber-400',  bg: 'bg-amber-500/20',  label: '待审核' },
}
const STATUS_DOT: Record<string, string> = {
  ready:     'bg-gray-500',
  running:   'bg-blue-500 animate-pulse',
  succeeded: 'bg-green-500',
  failed:    'bg-red-500',
  review:    'bg-amber-500',
}

interface Props {
  drama: Drama
  episodes: Episode[]
  onRefresh: () => void
}

export function EpisodeTab({ drama, episodes, onRefresh }: Props) {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchResult, setBatchResult] = useState<{ ok: number; fail: number } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploads, setUploads] = useState<UploadItem[]>([])

  // ── Video upload ──
  const handleFilesSelected = (files: FileList | null) => {
    if (!files || files.length === 0) return

    const items: UploadItem[] = [...files]
      .map(f => {
        const stem = f.name.replace(/\.[^.]+$/, '')
        const m = stem.match(/^0*(\d+)$/) || stem.match(/^第0*(\d+)集$/)
        return {
          file: f,
          episode: m ? m[1] : '',
          progress: 0,
          status: 'pending' as const,
          error: m ? undefined : '文件名格式不正确',
        }
      })
      .sort((a, b) => Number(a.episode || 0) - Number(b.episode || 0))

    setUploads(items)
    // Start uploading valid files
    uploadFiles(items)
  }

  const uploadFiles = async (items: UploadItem[]) => {
    for (let i = 0; i < items.length; i++) {
      if (items[i].error) continue
      // Mark uploading
      setUploads(prev => prev.map((u, j) => j === i ? { ...u, status: 'uploading' } : u))

      try {
        const form = new FormData()
        form.append('file', items[i].file)
        const xhr = new XMLHttpRequest()
        await new Promise<void>((resolve, reject) => {
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              const pct = Math.round((e.loaded / e.total) * 100)
              setUploads(prev => prev.map((u, j) => j === i ? { ...u, progress: pct } : u))
            }
          }
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              setUploads(prev => prev.map((u, j) => j === i ? { ...u, status: 'done', progress: 100 } : u))
              resolve()
            } else {
              const msg = (() => { try { return JSON.parse(xhr.responseText)?.detail } catch { return xhr.statusText } })()
              setUploads(prev => prev.map((u, j) => j === i ? { ...u, status: 'error', error: msg || '上传失败' } : u))
              reject()
            }
          }
          xhr.onerror = () => {
            setUploads(prev => prev.map((u, j) => j === i ? { ...u, status: 'error', error: '网络错误' } : u))
            reject()
          }
          xhr.open('POST', `/api/dramas/${drama.id}/videos`)
          xhr.send(form)
        })
      } catch { /* error already set in state */ }
    }
    // Refresh after all done
    onRefresh()
  }

  const clearUploads = () => {
    setUploads([])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // Selection helpers
  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const selectAll = () => setSelected(new Set(episodes.map(e => e.id)))
  const selectNone = () => setSelected(new Set())
  const allSelected = episodes.length > 0 && selected.size === episodes.length

  const selectedEpisodes = episodes.filter(e => selected.has(e.id))
  const runnableSelected = selectedEpisodes.filter(e => e.status !== 'running')

  // Stats
  const stats = {
    total: episodes.length,
    done: episodes.filter(e => e.status === 'succeeded' || e.dubbed_video).length,
    running: episodes.filter(e => e.status === 'running').length,
    failed: episodes.filter(e => e.status === 'failed').length,
  }

  // Batch run pipeline
  const handleBatchRun = async (toPhase?: string) => {
    if (runnableSelected.length === 0) return
    setBatchRunning(true)
    setBatchResult(null)
    let ok = 0, fail = 0
    for (const ep of runnableSelected) {
      try {
        await postJson(`/episodes/${encodeURIComponent(drama.name)}/${encodeURIComponent(ep.episode)}/pipeline/run`, {
          ...(toPhase ? { to_phase: toPhase } : {}),
        })
        ok++
      } catch {
        fail++
      }
    }
    setBatchResult({ ok, fail })
    setBatchRunning(false)
    selectNone()
    setTimeout(() => onRefresh(), 1000)
  }

  return (
    <div>
      {/* Stats bar + Upload button */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex gap-6 text-sm">
          {episodes.length > 0 && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-gray-500">总计</span>
                <span className="font-medium">{stats.total}</span>
              </div>
              {stats.done > 0 && (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span className="text-gray-500">完成</span>
                  <span className="font-medium text-green-400">{stats.done}</span>
                </div>
              )}
              {stats.running > 0 && (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                  <span className="text-gray-500">进行中</span>
                  <span className="font-medium text-blue-400">{stats.running}</span>
                </div>
              )}
              {stats.failed > 0 && (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="text-gray-500">失败</span>
                  <span className="font-medium text-red-400">{stats.failed}</span>
                </div>
              )}
            </>
          )}
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            multiple
            className="hidden"
            onChange={e => { handleFilesSelected(e.target.files); e.target.value = '' }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="h-8 px-4 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 transition-colors flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
            </svg>
            上传视频
          </button>
        </div>
      </div>

      {/* Upload progress list */}
      {uploads.length > 0 && (
        <div className="mb-4 rounded-xl border border-white/[0.06] bg-[#14161e] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.04]">
            <span className="text-xs font-medium text-gray-400">
              上传队列（{uploads.filter(u => u.status === 'done').length}/{uploads.length}）
            </span>
            {uploads.every(u => u.status === 'done' || u.status === 'error') && (
              <button onClick={clearUploads} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">关闭</button>
            )}
          </div>
          {uploads.map((u, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-2 border-b border-white/[0.03] last:border-b-0">
              {/* Status icon */}
              <div className="w-5 shrink-0 flex justify-center">
                {u.status === 'done' ? (
                  <svg className="w-4 h-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                  </svg>
                ) : u.status === 'error' ? (
                  <svg className="w-4 h-4 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
                  </svg>
                ) : u.status === 'uploading' ? (
                  <div className="w-3.5 h-3.5 rounded-full border-2 border-blue-400 border-t-transparent animate-spin" />
                ) : (
                  <div className="w-2 h-2 rounded-full bg-gray-600" />
                )}
              </div>
              {/* Filename + episode */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm truncate">{u.file.name}</span>
                  {u.episode && <span className="text-xs text-gray-500 shrink-0">第 {u.episode} 集</span>}
                </div>
                {u.error && <p className="text-xs text-red-400 mt-0.5">{u.error}</p>}
              </div>
              {/* Progress bar */}
              {(u.status === 'uploading' || u.status === 'done') && (
                <div className="w-24 shrink-0">
                  <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${u.status === 'done' ? 'bg-green-400' : 'bg-blue-400'}`}
                      style={{ width: `${u.progress}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Progress bar */}
      {episodes.length > 0 && (
        <div className="h-1 rounded-full bg-[#1a1d28] mb-4 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-green-500 to-emerald-400 transition-all duration-500"
            style={{ width: `${(stats.done / stats.total) * 100}%` }}
          />
        </div>
      )}

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 mb-4 px-4 py-2.5 rounded-xl bg-blue-500/[0.08] border border-blue-500/20">
          <button
            onClick={selectNone}
            className="text-gray-400 hover:text-gray-200 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <span className="text-sm text-blue-300">已选择 {selected.size} 集</span>
          <div className="flex-1" />
          <button
            onClick={() => handleBatchRun('burn')}
            disabled={batchRunning || runnableSelected.length === 0}
            className="h-7 px-4 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {batchRunning ? '提交中...' : '开始配音'}
          </button>
        </div>
      )}

      {/* Batch result toast */}
      {batchResult && (
        <div className={`flex items-center gap-2 mb-4 px-4 py-2 rounded-xl text-sm ${
          batchResult.fail > 0 ? 'bg-amber-500/10 text-amber-300' : 'bg-emerald-500/10 text-emerald-300'
        }`}>
          <span>已提交 {batchResult.ok} 集</span>
          {batchResult.fail > 0 && <span>（{batchResult.fail} 集失败）</span>}
          <div className="flex-1" />
          <button onClick={() => setBatchResult(null)} className="text-xs opacity-60 hover:opacity-100">关闭</button>
        </div>
      )}

      {/* Episode list */}
      {episodes.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-gray-500 mb-1">暂无剧集</p>
          <p className="text-xs text-gray-600">点击上方"上传视频"按钮添加剧集</p>
        </div>
      ) : (
        <div className="rounded-xl border border-white/[0.04] overflow-hidden">
          {/* Table header */}
          <div className="grid grid-cols-[32px_1fr_100px_auto_40px] gap-3 px-5 py-2.5 text-[11px] font-medium uppercase tracking-wider text-gray-600 bg-[#12141c] border-b border-white/[0.04]">
            <label className="flex items-center justify-center cursor-pointer">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() => allSelected ? selectNone() : selectAll()}
                className="w-3.5 h-3.5 rounded border-gray-600 bg-transparent text-blue-500 focus:ring-0 focus:ring-offset-0 cursor-pointer"
              />
            </label>
            <span>剧集</span>
            <span className="text-center">状态</span>
            <span>下载</span>
            <span />
          </div>

          {/* Episode rows */}
          {episodes.map((ep, i) => {
            const meta = STATUS_META[ep.status] ?? STATUS_META.ready
            const dot = STATUS_DOT[ep.status] ?? 'bg-gray-600'
            const isSelected = selected.has(ep.id)
            return (
              <div
                key={ep.id}
                className={`group grid grid-cols-[32px_1fr_100px_auto_40px] gap-3 items-center px-5 py-3 hover:bg-[#1a1d28] transition-colors ${
                  i < episodes.length - 1 ? 'border-b border-white/[0.03]' : ''
                } ${isSelected ? 'bg-blue-500/[0.04]' : ''}`}
              >
                {/* Checkbox */}
                <label className="flex items-center justify-center cursor-pointer" onClick={e => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleSelect(ep.id)}
                    className="w-3.5 h-3.5 rounded border-gray-600 bg-transparent text-blue-500 focus:ring-0 focus:ring-offset-0 cursor-pointer"
                  />
                </label>

                {/* Episode name */}
                <button
                  onClick={() => navigate(`/ide/${ep.id}`)}
                  className="flex items-center gap-3 min-w-0 text-left"
                >
                  <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
                  <span className="font-medium text-sm truncate group-hover:text-white transition-colors">
                    第 {ep.episode} 集
                  </span>
                  {ep.dubbed_video && (
                    <span className="shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded bg-green-500/10 text-green-400 border border-green-500/20">
                      已配音
                    </span>
                  )}
                </button>

                {/* Status */}
                <div className="flex justify-center">
                  <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${meta.bg} ${meta.color}`}>
                    {meta.label}
                  </span>
                </div>

                {/* Downloads */}
                <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
                  {ep.dubbed_video ? (
                    <>
                      <a
                        href={`/api/export/${ep.id}/zh.srt`}
                        download
                        className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-500/10 text-gray-400 hover:text-gray-200 hover:bg-gray-500/20 transition-colors"
                        title="中文字幕"
                      >
                        ZH
                      </a>
                      <a
                        href={`/api/export/${ep.id}/en.srt`}
                        download
                        className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-500/10 text-gray-400 hover:text-gray-200 hover:bg-gray-500/20 transition-colors"
                        title="英文字幕"
                      >
                        EN
                      </a>
                      <a
                        href={`/api/export/${ep.id}/dubbed.mp4`}
                        download
                        className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/20 transition-colors"
                        title="译制视频"
                      >
                        MP4
                      </a>
                    </>
                  ) : (
                    <span className="text-[10px] text-gray-700">--</span>
                  )}
                </div>

                {/* Arrow */}
                <button
                  onClick={() => navigate(`/ide/${ep.id}`)}
                  className="flex justify-end"
                >
                  <svg className="w-4 h-4 text-gray-700 group-hover:text-gray-400 group-hover:translate-x-0.5 transition-all" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                  </svg>
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
