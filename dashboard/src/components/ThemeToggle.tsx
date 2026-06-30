import { useTheme } from '../hooks/useTheme'

export default function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()

  return (
    <button className="theme-toggle" onClick={toggleTheme} aria-label="Toggle theme">
      <span>{theme === 'light' ? '☀️' : '🌙'}</span>
      <span>{theme === 'light' ? '亮色' : '暗色'}</span>
    </button>
  )
}
