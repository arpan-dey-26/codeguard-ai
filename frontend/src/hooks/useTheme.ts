import { useCallback, useEffect, useState } from 'react'
import type { Theme } from '@/types/domain'

const STORAGE_KEY = 'codeguard-theme'

function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    // Private browsing and blocked site data both throw here. The app must
    // still render, so a failure to read simply means "no preference".
    return null
  }
}

function currentDocumentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

/**
 * Read and set the theme.
 *
 * The initial value is read from the DOM, not from storage: the inline script
 * in index.html has already resolved the correct theme before React mounts,
 * so trusting the DOM keeps React and the painted page in agreement.
 */
export function useTheme(): { theme: Theme; setTheme: (next: Theme) => void; toggle: () => void } {
  const [theme, setThemeState] = useState<Theme>(currentDocumentTheme)

  useEffect(() => {
    const stored = readStoredTheme()
    if (stored && stored !== theme) setThemeState(stored)
    // Runs once: later changes go through setTheme.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setTheme = useCallback((next: Theme) => {
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Preference is not persisted; the session still works.
    }
    setThemeState(next)
  }, [])

  const toggle = useCallback(
    () => setTheme(currentDocumentTheme() === 'dark' ? 'light' : 'dark'),
    [setTheme],
  )

  return { theme, setTheme, toggle }
}
