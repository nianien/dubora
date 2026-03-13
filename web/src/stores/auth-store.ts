import { create } from 'zustand'

interface AuthUser {
  id: number
  email: string
  name: string
  picture?: string
}

interface AuthState {
  user: AuthUser | null
  checked: boolean
  loading: boolean
  checkAuth: () => Promise<void>
  logout: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  checked: false,
  loading: true,

  checkAuth: async () => {
    set({ loading: true })
    try {
      const res = await fetch('/api/auth/me')
      const data = await res.json()
      set({ user: data.authenticated ? data.user : null, checked: true, loading: false })
    } catch {
      set({ user: null, checked: true, loading: false })
    }
  },

  logout: async () => {
    await fetch('/api/auth/logout', { method: 'POST' })
    set({ user: null })
  },
}))
