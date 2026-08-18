interface PlayBarProps {
  disabled: boolean
  disabledReason?: string
  loading?: boolean
  onPlay: () => void
}

export default function PlayBar({ disabled, disabledReason, loading, onPlay }: PlayBarProps) {
  return (
    <div className="sticky bottom-4 flex justify-center">
      <div className="flex items-center gap-3 rounded-2xl border border-surface-700 bg-surface-900/90 px-4 py-3 shadow-xl shadow-black/40 backdrop-blur">
        {disabled && disabledReason && <span className="text-sm text-surface-400">{disabledReason}</span>}
        <button
          type="button"
          disabled={disabled || loading}
          onClick={onPlay}
          className="rounded-xl bg-accent-500 px-6 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:bg-surface-600 disabled:text-surface-400"
        >
          {loading ? 'Running…' : '▶ Play'}
        </button>
      </div>
    </div>
  )
}
