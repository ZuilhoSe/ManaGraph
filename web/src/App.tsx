import { useState } from 'react'
import PromptPanel from './components/PromptPanel'
import ConfigPanel from './components/ConfigPanel'
import DeckCardList from './components/DeckCardList'
import PlayBar from './components/PlayBar'
import PayloadPreview from './components/PayloadPreview'
import CollectionView from './components/CollectionView'
import { buildPayload } from './lib/buildPayload'
import { emptyFormState } from './types'
import type { DeckFormState } from './types'

type Tab = 'build' | 'collection'

export default function App() {
  const [tab, setTab] = useState<Tab>('build')
  const [form, setForm] = useState<DeckFormState>(emptyFormState)
  const [payload, setPayload] = useState<unknown>(null)

  function set<K extends keyof DeckFormState>(key: K, value: DeckFormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const canPlay = form.query.trim().length > 0

  function handlePlay() {
    if (!canPlay) return
    setPayload(buildPayload(form))
  }

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

            {payload !== null && <PayloadPreview payload={payload} onClose={() => setPayload(null)} />}
          </div>

          <div className="mt-6">
            <PlayBar
              disabled={!canPlay}
              disabledReason={!canPlay ? 'Describe what you want to do to enable play' : undefined}
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
