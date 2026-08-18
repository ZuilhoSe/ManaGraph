import { useState } from 'react'
import PromptPanel from './components/PromptPanel'
import ConfigPanel from './components/ConfigPanel'
import DeckCardList from './components/DeckCardList'
import PlayBar from './components/PlayBar'
import RunPanel from './components/RunPanel'
import type { RunStatus } from './components/RunPanel'
import CollectionView from './components/CollectionView'
import { buildPayload } from './lib/buildPayload'
import { runDeck } from './lib/api'
import type { DeckRunEvent } from './lib/api'
import { emptyFormState } from './types'
import type { DeckFormState } from './types'

type Tab = 'build' | 'collection'

export default function App() {
  const [tab, setTab] = useState<Tab>('build')
  const [form, setForm] = useState<DeckFormState>(emptyFormState)
  const [runEvents, setRunEvents] = useState<DeckRunEvent[]>([])
  const [runStatus, setRunStatus] = useState<RunStatus | 'idle'>('idle')
  const [runError, setRunError] = useState<string | undefined>()

  function set<K extends keyof DeckFormState>(key: K, value: DeckFormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const canPlay = form.query.trim().length > 0

  async function handlePlay() {
    if (!canPlay) return
    const payload = buildPayload(form)
    setRunEvents([])
    setRunError(undefined)
    setRunStatus('running')
    try {
      await runDeck(payload, (event) => {
        setRunEvents((prev) => [...prev, event])
        if (event.type === 'error') {
          setRunError(event.message)
          setRunStatus('error')
        }
      })
      setRunStatus((prev) => (prev === 'error' ? prev : 'done'))
    } catch (err) {
      setRunError(err instanceof Error ? err.message : String(err))
      setRunStatus('error')
    }
  }

  const isRunning = runStatus === 'running'

  return (
    <div className="mx-auto min-h-svh max-w-2xl px-4 py-6">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight text-surface-50">ManaGraph</h1>
        <p className="mt-0.5 text-sm text-surface-400">
          Set up the orchestrator's input before running the agent graph.
        </p>
      </header>

      <div className="mb-5 flex gap-1 border-b border-surface-700">
        {(
          [
            ['build', 'Build'],
            ['collection', 'Collection'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
              tab === id
                ? 'border-accent-500 text-surface-50'
                : 'border-transparent text-surface-400 hover:text-surface-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'build' ? (
        <>
          <div className="flex flex-col gap-4">
            <PromptPanel value={form.query} onChange={(v) => set('query', v)} />
            <ConfigPanel state={form} onChange={set} />
            <DeckCardList cards={form.cards} onChange={(cards) => set('cards', cards)} />

            {runStatus !== 'idle' && (
              <RunPanel events={runEvents} status={runStatus} errorMessage={runError} />
            )}
          </div>

          <div className="mt-6">
            <PlayBar
              disabled={!canPlay}
              disabledReason={!canPlay ? 'Describe what you want to do to enable play' : undefined}
              loading={isRunning}
              onPlay={handlePlay}
            />
          </div>
        </>
      ) : (
        <CollectionView />
      )}
    </div>
  )
}
