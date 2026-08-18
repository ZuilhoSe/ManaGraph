import { useState } from 'react'
import Section from './Section'

interface PayloadPreviewProps {
  payload: unknown
  onClose: () => void
}

// Until a backend is wired to the play button, this is the "output" of this
// screen: the JSON that would become the argument to initial_graph_state(query, deck).
export default function PayloadPreview({ payload, onClose }: PayloadPreviewProps) {
  const [copied, setCopied] = useState(false)
  const json = JSON.stringify(payload, null, 2)

  async function copy() {
    await navigator.clipboard.writeText(json)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Section
      title="Generated payload"
      subtitle="No backend wired up yet — this is what would be sent to the graph (main_agent.py)."
      className="border-accent-500/40"
    >
      <pre className="max-h-96 overflow-auto rounded-lg bg-surface-950 p-4 text-xs leading-relaxed text-surface-200">
        {json}
      </pre>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-3 py-1.5 text-sm text-surface-400 hover:text-surface-200"
        >
          Close
        </button>
        <button
          type="button"
          onClick={copy}
          className="rounded-lg bg-accent-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-600"
        >
          {copied ? 'Copied!' : 'Copy JSON'}
        </button>
      </div>
    </Section>
  )
}
