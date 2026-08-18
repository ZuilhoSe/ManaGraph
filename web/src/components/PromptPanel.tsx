import Section from './Section'

interface PromptPanelProps {
  value: string
  onChange: (value: string) => void
}

export default function PromptPanel({ value, onChange }: PromptPanelProps) {
  return (
    <Section
      title="What do you want to do"
      subtitle='Free text — e.g. "build a full Krenko deck focused on goblin tokens"'
    >
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Describe what you want ManaGraph to do with the deck..."
        rows={3}
        className="w-full resize-y rounded-xl border border-surface-600 bg-surface-800 p-4 text-base text-surface-50 placeholder:text-surface-400 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30"
      />
    </Section>
  )
}
