import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { DramaListPage } from './pages/DramaListPage'
import { DramaDetailPage } from './pages/DramaDetailPage'
import { IDEPage } from './pages/IDEPage'
import { CastingPage } from './pages/CastingPage'
import { GlossaryPage } from './pages/GlossaryPage'
import './index.css'

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: '#f87171', background: '#111', minHeight: '100vh' }}>
          <h2 style={{ marginBottom: 8 }}>Page crashed</h2>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: '#fbbf24' }}>
            {this.state.error.message}
          </pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 11, color: '#9ca3af', marginTop: 8 }}>
            {this.state.error.stack}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 16, padding: '6px 16px', background: '#374151', color: '#e5e7eb', border: 'none', borderRadius: 4, cursor: 'pointer' }}
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DramaListPage />} />
            <Route path="/drama/:dramaId" element={<DramaDetailPage />} />
            <Route path="/ide/:episodeId" element={<IDEPage />} />
            <Route path="/voices" element={<CastingPage />} />
            <Route path="/casting/:dramaId" element={<CastingPage />} />
            <Route path="/glossary" element={<GlossaryPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
)
