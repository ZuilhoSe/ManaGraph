import { useEffect, useRef, useState } from 'react'
import Section from './Section'
import type { DeckRunEvent, RunNode } from '../lib/api'

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}

const NODE_LABEL: Record<RunNode, string> = {
  architect: 'Architect',
  inventory: 'Inventory',
  solver: 'Solver',
  supervisor: 'Supervisor',
}

const NODE_COLOR: Record<RunNode, string> = {
  architect: 'border-accent-500/50 text-accent-400',
  inventory: 'border-emerald-500/50 text-emerald-400',
  solver: 'border-amber-500/50 text-amber-400',
  supervisor: 'border-fuchsia-500/50 text-fuchsia-400',
}

export type RunStatus = 'running' | 'done' | 'error' | 'stopped'

interface RunPanelProps {
  events: DeckRunEvent[]
  status: RunStatus
  errorMessage?: string
}

export default function RunPanel({ events, status, errorMessage }: RunPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState(false)
  const visible = events.filter((e) => e.type === 'log' || e.type === 'node' || e.type === 'node_start')
  const startTs = events[0]?.ts
  const lastTs = visible[visible.length - 1]?.ts
  const errorEvent = events.find((e) => e.type === 'error')
  const errorNode = errorEvent?.node ? (NODE_LABEL[errorEvent.node] ?? errorEvent.node) : undefined

  function copyLog() {
    const lines = errorEvent
      ? [...visible, errorEvent]
      : visible
    const text = lines
      .map((e) => {
        const elapsed = e.ts !== undefined && startTs !== undefined ? `+${formatElapsed(e.ts - startTs)} ` : ''
        if (e.type === 'node_start') return `${elapsed}▶ ${NODE_LABEL[e.node as RunNode] ?? e.node} started`
        if (e.type === 'node') return `${elapsed}[${NODE_LABEL[e.node as RunNode] ?? e.node}]\n${e.text}`
        if (e.type === 'error') {
          const node = e.node ? `${NODE_LABEL[e.node] ?? e.node} failed: ` : ''
          return `${elapsed}${node}${e.message}`
        }
        return `${elapsed}${e.text}`
      })
      .join('\n')
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  // Ticks once a second while running, purely so the "Xs since last update"
  // readout keeps moving -- that's the answer to "is this actually stuck?".
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (status !== 'running') return
    const id = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(id)
  }, [status])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events])

  return (
    <Section
      title="Agent run"
      subtitle={
        status === 'running'
          ? 'Working…'
          : status === 'error'
            ? 'Failed'
            : status === 'stopped'
              ? 'Stopped'
              : 'Finished'
      }
      actions={
        visible.length > 0 && (
          <button
            type="button"
            onClick={copyLog}
            className="shrink-0 rounded-lg border border-surface-600 px-2.5 py-1 text-xs text-surface-300 hover:bg-surface-800"
          >
            {copied ? 'Copied!' : 'Copy log'}
          </button>
        )
      }
    >
      <div
        ref={scrollRef}
        className="max-h-80 space-y-1.5 overflow-y-auto rounded-lg border border-surface-700 bg-surface-950/40 p-2 font-mono"
      >
        {visible.length === 0 && status === 'running' && (
          <p className="px-1 py-2 text-xs text-surface-400">Starting up…</p>
        )}
        {visible.map((event, i) =>
          event.type === 'log' ? (
            <p key={i} className="flex gap-2 px-1 text-xs break-words text-surface-400">
              {event.ts !== undefined && startTs !== undefined && (
                <span className="shrink-0 tabular-nums text-surface-600">
                  +{formatElapsed(event.ts - startTs)}
                </span>
              )}
              <span className="break-all">{event.text}</span>
            </p>
          ) : event.type === 'node_start' ? (
            <p
              key={i}
              className={`flex items-center gap-2 px-1 text-xs font-semibold ${NODE_COLOR[event.node as RunNode]?.split(' ')[1] ?? 'text-surface-300'}`}
            >
              {event.ts !== undefined && startTs !== undefined && (
                <span className="shrink-0 tabular-nums font-normal text-surface-600">
                  +{formatElapsed(event.ts - startTs)}
                </span>
              )}
              ▶ {NODE_LABEL[event.node as RunNode] ?? event.node} started
            </p>
          ) : (
            <div
              key={i}
              className={`rounded-lg border-l-2 bg-surface-800/60 px-3 py-2 ${NODE_COLOR[event.node as RunNode] ?? 'border-surface-600 text-surface-300'}`}
            >
              <div className="mb-1 flex items-center justify-between text-xs font-semibold tracking-wide uppercase">
                <span>{NODE_LABEL[event.node as RunNode] ?? event.node}</span>
                {event.ts !== undefined && startTs !== undefined && (
                  <span className="font-normal normal-case tabular-nums text-surface-500">
                    +{formatElapsed(event.ts - startTs)}
                  </span>
                )}
              </div>
              <pre className="max-h-40 overflow-y-auto font-sans text-xs whitespace-pre-wrap text-surface-200">
                {event.text}
              </pre>
            </div>
          ),
        )}
        {status === 'running' && (
          <div className="flex items-center gap-2 px-1 py-1 text-xs text-surface-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-500" />
            working… {lastTs !== undefined && `(${formatElapsed(now - lastTs)} since last update)`}
          </div>
        )}
      </div>

      {status === 'error' && (
        <p className="mt-2 text-sm text-red-400">
          {errorNode ? <span className="font-semibold">{errorNode} failed: </span> : null}
          {errorMessage}
        </p>
      )}

      {status === 'stopped' && <p className="mt-2 text-sm text-surface-400">Stopped by you.</p>}

      {status === 'done' && <RunResult events={events} />}
    </Section>
  )
}

function buildDecklistText(deck: Record<string, unknown> | undefined): string {
  if (!deck) return ''
  const commander = (deck.commander as string) || ''
  const cards = (deck.cards as Record<string, number>) || {}
  const sortedCards = Object.entries(cards)
    .filter(([, qty]) => qty > 0)
    .sort(([a], [b]) => a.localeCompare(b))

  const sections: string[] = []
  if (commander) sections.push(`Commander\n1 ${commander}`)
  if (sortedCards.length > 0) {
    sections.push(`Deck\n${sortedCards.map(([name, qty]) => `${qty} ${name}`).join('\n')}`)
  }
  return sections.join('\n\n')
}

function RunResult({ events }: { events: DeckRunEvent[] }) {
  const [copied, setCopied] = useState(false)
  const reversed = [...events].reverse()
  const supervisorDecision = reversed.find((e) => e.supervisor_decision)?.supervisor_decision
  const validation = reversed.find((e) => e.validation)?.validation
  const deck = reversed.find((e) => e.deck)?.deck
  const diff = reversed.find((e) => e.deck_diff)?.deck_diff
  const cards = deck?.cards as Record<string, number> | undefined
  const cardCount = cards ? Object.values(cards).reduce((a, b) => a + b, 0) : undefined

  function copyDecklist() {
    navigator.clipboard.writeText(buildDecklistText(deck))
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="mt-3 rounded-lg border border-surface-700 bg-surface-800/60 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium text-surface-100">Supervisor decision</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-semibold uppercase ${
            supervisorDecision === 'APPROVED'
              ? 'bg-emerald-500/20 text-emerald-400'
              : 'bg-amber-500/20 text-amber-400'
          }`}
        >
          {supervisorDecision ?? '—'}
        </span>
      </div>
      {cardCount !== undefined && (
        <div className="mt-1 flex items-center justify-between">
          <p className="text-xs text-surface-400">{cardCount} cards in the final deck</p>
          {cards && (
            <button
              type="button"
              onClick={copyDecklist}
              className="shrink-0 rounded-lg border border-surface-600 px-2.5 py-1 text-xs text-surface-300 hover:bg-surface-700"
            >
              {copied ? 'Copied!' : 'Copy decklist'}
            </button>
          )}
        </div>
      )}
      {validation?.error && <p className="mt-2 text-xs text-red-400">{validation.error}</p>}
      {validation?.warnings && validation.warnings.length > 0 && (
        <ul className="mt-2 list-inside list-disc text-xs text-amber-300">
          {validation.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      {diff && (diff.removed.length > 0 || diff.added.length > 0 || diff.commander_changed) && (
        <div className="mt-3 border-t border-surface-700 pt-3">
          <p className="text-xs font-medium text-surface-300">
            Changes vs. the deck you started with ({diff.removed_count} out / {diff.added_count} in)
          </p>
          {diff.commander_changed && (
            <p className="mt-1.5 text-xs text-surface-400">
              Commander: <span className="text-red-400 line-through">{diff.commander_changed.from || '—'}</span>{' '}
              → <span className="text-emerald-400">{diff.commander_changed.to || '—'}</span>
            </p>
          )}
          {(diff.removed.length > 0 || diff.added.length > 0) && (
            <div className="mt-1.5 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {diff.removed.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-red-400">Out</p>
                  <ul className="mt-1 space-y-0.5 text-xs text-surface-300">
                    {diff.removed.map((c, i) => (
                      <li key={i}>
                        {c.quantity > 1 ? `${c.quantity}x ` : ''}
                        {c.name}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {diff.added.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-emerald-400">In</p>
                  <ul className="mt-1 space-y-0.5 text-xs text-surface-300">
                    {diff.added.map((c, i) => (
                      <li key={i}>
                        {c.quantity > 1 ? `${c.quantity}x ` : ''}
                        {c.name}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
