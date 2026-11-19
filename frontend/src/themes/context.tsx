import type { ThemeComponents } from './definition'

export function useThemeComponent<K extends keyof ThemeComponents>(_key: K, fallback: ThemeComponents[K]): ThemeComponents[K] {
  return fallback
}
