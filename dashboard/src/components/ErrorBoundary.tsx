import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: string
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: '' }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error: error.message }
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div style={{
          padding: 40,
          textAlign: 'center',
          color: 'var(--color-text-secondary)',
        }}>
          <h3 style={{ marginBottom: 8, color: 'var(--color-danger)' }}>
            页面渲染错误
          </h3>
          <pre style={{
            fontSize: '0.75rem',
            fontFamily: 'var(--font-mono)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {this.state.error}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}
