import { useEffect, useRef, useState } from 'react'
import { Field, TextField } from './fields'
import { searchCommanders } from '../lib/api'
import type { CommanderCandidate } from '../lib/api'

interface CommanderFieldProps {
  value: string
  onChange: (value: string) => void
}

// Suggestions are best-effort: if the backend or the card index isn't ready
// yet, the fetch just silently yields nothing and the field still works as a
// plain text input.
export default function CommanderField({ value, onChange }: CommanderFieldProps) {
  const [suggestions, setSuggestions] = useState<CommanderCandidate[]>([])
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (value.trim().length < 2) {
      setSuggestions([])
      return
    }
    const controller = new AbortController()
    const timer = setTimeout(() => {
      searchCommanders(value, controller.signal)
        .then(setSuggestions)
        .catch(() => {})
    }, 200)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [value])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  return (
    <Field label="Commander">
      <div ref={containerRef} className="relative">
        <TextField
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          placeholder="e.g. Krenko, Mob Boss"
          autoComplete="off"
        />
        {open && suggestions.length > 0 && (
          <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-surface-600 bg-surface-800 py-1 shadow-xl">
            {suggestions.map((c) => (
              <li key={c.name}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(c.name)
                    setOpen(false)
                  }}
                  className="flex w-full flex-col px-3 py-1.5 text-left text-sm text-surface-100 hover:bg-surface-700"
                >
                  <span>{c.name}</span>
                  <span className="text-xs text-surface-400">{c.type_line}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Field>
  )
}
