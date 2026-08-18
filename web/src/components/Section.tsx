import type { ReactNode } from 'react'

interface SectionProps {
  title: string
  subtitle?: string
  children: ReactNode
  className?: string
}

// Default "card" chrome for any block on the screen.
// Change the look of every block at once by editing only this file.
export default function Section({ title, subtitle, children, className = '' }: SectionProps) {
  return (
    <section
      className={`rounded-2xl border border-surface-700 bg-surface-900 p-4 shadow-lg shadow-black/20 ${className}`}
    >
      <header className="mb-3">
        <h2 className="text-sm font-semibold tracking-wide text-surface-50 uppercase">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-surface-400">{subtitle}</p>}
      </header>
      {children}
    </section>
  )
}
