/** Generic right-click context menu */
import { useEffect, useRef } from 'react'

export interface ContextMenuItem {
  label: string
  shortcut?: string
  onClick: () => void
  disabled?: boolean
  dividerAfter?: boolean
}

interface ContextMenuProps {
  x: number
  y: number
  items: ContextMenuItem[]
  onClose: () => void
}

export function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)

  // Close on click outside or Escape
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKey, true)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey, true)
    }
  }, [onClose])

  // Adjust position to keep menu within viewport
  useEffect(() => {
    if (!menuRef.current) return
    const rect = menuRef.current.getBoundingClientRect()
    const el = menuRef.current
    if (rect.right > window.innerWidth) {
      el.style.left = `${x - rect.width}px`
    }
    if (rect.bottom > window.innerHeight) {
      el.style.top = `${y - rect.height}px`
    }
  }, [x, y])

  return (
    <div
      ref={menuRef}
      className="fixed z-[100] bg-gray-800 border border-gray-600 rounded-lg shadow-xl py-1 min-w-[220px] select-none"
      style={{ left: x, top: y }}
    >
      {items.map((item, i) => (
        <div key={i}>
          <button
            className={`
              w-full text-left px-3 py-1.5 text-sm flex items-center justify-between gap-4
              ${item.disabled
                ? 'text-gray-500 cursor-default'
                : 'text-gray-200 hover:bg-gray-700 cursor-pointer'}
            `}
            disabled={item.disabled}
            onClick={(e) => {
              e.stopPropagation()
              if (!item.disabled) {
                item.onClick()
                onClose()
              }
            }}
          >
            <span>{item.label}</span>
            {item.shortcut && (
              <span className="text-xs text-gray-500 ml-4">{item.shortcut}</span>
            )}
          </button>
          {item.dividerAfter && (
            <div className="border-t border-gray-600 my-1" />
          )}
        </div>
      ))}
    </div>
  )
}
