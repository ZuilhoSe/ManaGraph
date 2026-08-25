import { useEffect, useRef, useState } from 'react'
import PromptPanel from './components/PromptPanel'
import ConfigPanel from './components/ConfigPanel'
import DeckCardList from './components/DeckCardList'
import PlayBar from './components/PlayBar'
import RunPanel from './components/RunPanel'
import type { RunStatus } from './components/RunPanel'
import CollectionView from './components/CollectionView'
import AnalysisView from './components/AnalysisView'
import ImportExportBar from './components/ImportExportBar'
import { buildPayload } from './lib/buildPayload'
import { cancelRun, fetchDeckPool, runDeck } from './lib/api'
import type { DeckRunEvent } from './lib/api'
import { emptyFormState } from './types'
import type { DeckFormState } from './types'

type Tab = 'build' | 'collection' | 'analysis'

export default function App() {
  const [tab, setTab] = useState<Tab>('build')
  // Collection/Analysis mount lazily on first visit, then stay mounted (see
  // the tabs divs below) -- mounting them eagerly on load would fire their
  // data fetches (saved decks + inventory) even for a session that never
  // opens that tab.
  const [visitedCollection, setVisitedCollection] = useState(false)
  const [visitedAnalysis, setVisitedAnalysis] = useState(false)
  useEffect(() => {
    if (tab === 'collection') setVisitedCollection(true)
    if (tab === 'analysis') setVisitedAnalysis(true)
  }, [tab])
  const [form, setForm] = useState<DeckFormState>(emptyFormState)
  const [runEvents, setRunEvents] = useState<DeckRunEvent[]>([])
  const [runStatus, setRunStatus] = useState<RunStatus | 'idle'>('idle')
  const [runError, setRunError] = useState<string | undefined>()
  // Both are per-run and only read from handleStop, so a ref (not state) is enough --
  // they never need to trigger a re-render on their own.
  const abortRef = useRef<AbortController | null>(null)
  const runIdRef = useRef<string | null>(null)

  function set<K extends keyof DeckFormState>(key: K, value: DeckFormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const canPlay = form.query.trim().length > 0

  async function handlePlay() {
    if (!canPlay) return
    let pool: Record<string, number> | undefined
    if (form.poolDeckNames.length > 0) {
      try {
        pool = (await fetchDeckPool(form.poolDeckNames)).pool
      } catch (err) {
        setRunError(err instanceof Error ? err.message : String(err))
        setRunStatus('error')
        return
      }
    }
    const payload = buildPayload(form, pool)
    const controller = new AbortController()
    abortRef.current = controller
    runIdRef.current = null
    setRunEvents([])
    setRunError(undefined)
    setRunStatus('running')
    try {
      await runDeck(
        payload,
        (event) => {
          if (event.type === 'run_id' && event.run_id) runIdRef.current = event.run_id
          setRunEvents((prev) => [...prev, event])
          if (event.type === 'error') {
            setRunError(event.message)
            setRunStatus('error')
          }
        },
        controller.signal,
      )
      setRunStatus((prev) => (prev === 'error' ? prev : 'done'))
    } catch (err) {
      if (controller.signal.aborted) {
        setRunStatus('stopped')
      } else {
        setRunError(err instanceof Error ? err.message : String(err))
        setRunStatus('error')
      }
    } finally {
      abortRef.current = null
    }
  }

  // Stops the UI from waiting instantly (aborting the fetch), and best-effort
  // asks the server to stop starting further graph nodes for this run -- it
  // can't interrupt an LLM call already in flight, only the fetch abort is
  // instant and guaranteed; see cancelRun()'s and deck_run.py's own comments.
  function handleStop() {
    if (runIdRef.current) cancelRun(runIdRef.current)
    abortRef.current?.abort()
  }

  const isRunning = runStatus === 'running'

  return (
    <div className="mx-auto min-h-svh max-w-2xl px-4 py-6">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-surface-50">ManaGraph</h1>
          <p className="mt-0.5 text-sm text-surface-400">
            Set up the orchestrator's input before running the agent graph.
          </p>
        </div>
        {tab === 'build' && <ImportExportBar form={form} onImport={setForm} />}
      </header>

      <div className="mb-5 flex gap-1 border-b border-surface-700">
        {(
          [
            ['build', 'Build'],
            ['collection', 'Collection'],
            ['analysis', 'Analysis'],
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

      {/* Once mounted, a tab stays mounted (hidden via CSS, not unmounted) so
          switching tabs never discards in-progress state that only lives
          locally in a component -- e.g. an open "Add deck" form in
          Collection, or the paste-list buffer in DeckCardList. Only
          `form`/`runEvents` live in App itself, but plenty of child-local
          state doesn't, and unmounting would silently reset it every time,
          indistinguishable from a reload. Build mounts immediately since
          it's the default tab; Collection mounts lazily on first visit
          (visitedCollection) so a session that never opens it doesn't pay
          for its data fetches. */}
      <div className={tab === 'build' ? 'contents' : 'hidden'}>
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
            onStop={handleStop}
          />
        </div>
      </div>
      {visitedCollection && (
        <div className={tab === 'collection' ? 'contents' : 'hidden'}>
          <CollectionView />
        </div>
      )}
      {visitedAnalysis && (
        <div className={tab === 'analysis' ? 'contents' : 'hidden'}>
          <AnalysisView />
        </div>
      )}
    </div>
  )
}
