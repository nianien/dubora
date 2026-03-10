import { Outlet, useLocation, useNavigate } from 'react-router-dom'

const NAV_ITEMS = [
  {
    key: 'workbench',
    label: '工作台',
    path: '/',
    match: (p: string) => p === '/' || p.startsWith('/drama/'),
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 12 8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25" />
      </svg>
    ),
  },
  {
    key: 'voices',
    label: '音色库',
    path: '/voices',
    match: (p: string) => p.startsWith('/casting/') || p.startsWith('/voices'),
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
      </svg>
    ),
  },
  {
    key: 'glossary',
    label: '术语表',
    path: '/glossary',
    match: (p: string) => p.startsWith('/glossary'),
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
      </svg>
    ),
  },
]

export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const isIDE = location.pathname.startsWith('/ide/')
  const sidebarW = isIDE ? 'w-12' : 'w-16'

  return (
    <div className="h-screen flex bg-[#0a0b10] text-gray-100 overflow-hidden">
      {/* ── Sidebar ── */}
      <aside className={`${sidebarW} shrink-0 flex flex-col border-r border-white/[0.06] bg-[#0c0d12] transition-all duration-200`}>
        {/* Logo */}
        <div className="flex flex-col items-center pt-4 pb-5">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center text-[10px] font-bold shadow-lg shadow-blue-500/20">
            D
          </div>
          {!isIDE && (
            <span className="text-[9px] font-semibold text-gray-500 mt-1.5 tracking-tight">Dubora</span>
          )}
        </div>

        {/* Nav items */}
        <nav className="flex-1 flex flex-col items-center gap-1 px-1">
          {NAV_ITEMS.map(item => {
            const active = item.match(location.pathname)
            return (
              <button
                key={item.key}
                onClick={() => navigate(item.path)}
                className={`relative w-full flex flex-col items-center py-2 rounded-lg transition-colors ${
                  active
                    ? 'text-blue-400 bg-blue-500/[0.08]'
                    : 'text-gray-600 hover:text-gray-400 hover:bg-white/[0.03]'
                }`}
              >
                {active && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-4 rounded-r bg-blue-400" />
                )}
                {item.icon}
                {!isIDE && (
                  <span className="text-[9px] mt-1">{item.label}</span>
                )}
              </button>
            )
          })}
        </nav>

        {/* Settings at bottom */}
        <div className="flex flex-col items-center pb-4 px-1">
          <button className="w-full flex flex-col items-center py-2 rounded-lg text-gray-600 hover:text-gray-400 hover:bg-white/[0.03] transition-colors">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            </svg>
            {!isIDE && (
              <span className="text-[9px] mt-1">设置</span>
            )}
          </button>
        </div>
      </aside>

      {/* ── Main content ── */}
      <div className="flex-1 min-w-0 overflow-auto">
        <Outlet />
      </div>
    </div>
  )
}
