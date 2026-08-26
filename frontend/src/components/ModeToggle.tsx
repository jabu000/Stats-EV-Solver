import type { Mode } from '../lib/types'

/**
 * The Best Value / Most Likely switch.
 *
 * These answer genuinely different questions, so the labels spell that out: one ranks
 * by edge over the break-even, the other by raw probability of hitting.
 */
export function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div
      role="radiogroup"
      aria-label="Ranking mode"
      className="inline-flex rounded-lg border border-ink-700 bg-ink-850 p-1"
    >
      {(
        [
          { id: 'value', label: 'Best Value', hint: 'Ranked by edge over break-even' },
          { id: 'likely', label: 'Most Likely', hint: 'Ranked by chance of hitting' },
        ] as const
      ).map((option) => (
        <button
          key={option.id}
          role="radio"
          aria-checked={mode === option.id}
          title={option.hint}
          onClick={() => onChange(option.id)}
          className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
            mode === option.id
              ? 'bg-emerald-500 text-ink-950'
              : 'text-slate-300 hover:text-white'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
