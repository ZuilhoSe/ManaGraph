import { useRef, useState } from 'react'
import { buildPayload } from '../lib/buildPayload'
import { downloadJson, exportFilename, parseImportedPayload } from '../lib/importExport'
import type { DeckFormState } from '../types'

interface ImportExportBarProps {
  form: DeckFormState
  onImport: (form: DeckFormState) => void
}

// Prompt + deck + configs, round-tripped as one JSON file -- so a manual test
// run doesn't mean retyping or copy-pasting the whole setup every time. Same
// {query, deck} shape as `python src/main_agent.py --export/--import`.
export default function ImportExportBar({ form, onImport }: ImportExportBarProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | undefined>()

  function handleExport() {
    downloadJson(exportFilename(form), buildPayload(form))
  }

  async function handleFileChange(file: File | undefined) {
    if (!file) return
    try {
      const text = await file.text()
      onImport(parseImportedPayload(JSON.parse(text)))
      setError(undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that file.')
    }
  }

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-xs text-red-400">{error}</span>}
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        className="rounded-lg border border-surface-600 px-3 py-1.5 text-sm text-surface-200 hover:border-accent-500 hover:text-surface-50"
      >
        Import JSON
      </button>
      <button
        type="button"
        onClick={handleExport}
        className="rounded-lg border border-surface-600 px-3 py-1.5 text-sm text-surface-200 hover:border-accent-500 hover:text-surface-50"
      >
        Export JSON
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          void handleFileChange(file)
        }}
      />
    </div>
  )
}
