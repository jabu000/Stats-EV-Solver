import type { BoardFilters, Mode } from '../lib/types'
import { ModeToggle } from './ModeToggle'

export interface FilterState {
  team: string
  game: string
  position: string
  search: string
  minEdge: number
  minProbability: number
}

interface Props {
  filters: BoardFilters
  state: FilterState
  mode: Mode
  onState: (state: FilterState) => void
  onMode: (mode: Mode) => void
  onRefresh: () => void
  busy: boolean
}

export function FilterBar({ filters, state, mode, onState, onMode, onRefresh, busy }: Props) {
  const set = <K extends keyof FilterState>(key: K, value: FilterState[K]) =>
    onState({ ...state, [key]: value })

  const active =
    state.team || state.game || state.position || state.search ||
    state.minEdge > 0 || state.minProbability > 0

  return (
    <div className="border-b border-ink-800 bg-ink-900/30">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-end gap-3 px-4 py-3">
        <Field label="Search player">
          <input
            value={state.search}
            onChange={(event) => set('search', event.target.value)}
            placeholder="Name…"
            className="w-44 rounded-md border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-emerald-500/60 focus:outline-none"
          />
        </Field>

        <Field label="Team">
          <Select value={state.team} onChange={(v) => set('team', v)} options={filters.teams} allLabel="All teams" />
        </Field>

        <Field label="Game">
          <Select value={state.game} onChange={(v) => set('game', v)} options={filters.games} allLabel="All games" wide />
        </Field>

        {filters.positions.length > 0 && (
          <Field label="Position">
            <Select value={state.position} onChange={(v) => set('position', v)} options={filters.positions} allLabel="All" />
          </Field>
        )}

        <Field label={`Min edge ${(state.minEdge * 100).toFixed(0)}%`}>
          <input
            type="range" min={0} max={0.2} step={0.005}
            value={state.minEdge}
            onChange={(event) => set('minEdge', Number(event.target.value))}
            className="w-28 accent-emerald-500"
          />
        </Field>

        <Field label={`Min probability ${(state.minProbability * 100).toFixed(0)}%`}>
          <input
            type="range" min={0} max={0.95} step={0.01}
            value={state.minProbability}
            onChange={(event) => set('minProbability', Number(event.target.value))}
            className="w-28 accent-emerald-500"
          />
        </Field>

        {active && (
          <button
            onClick={() => onState({ team: '', game: '', position: '', search: '', minEdge: 0, minProbability: 0 })}
            className="rounded-md border border-ink-700 px-3 py-1.5 text-sm text-slate-400 hover:text-white"
          >
            Clear
          </button>
        )}

        <div className="ml-auto flex items-end gap-3">
          <ModeToggle mode={mode} onChange={onMode} />
          <button
            onClick={onRefresh}
            disabled={busy}
            className="rounded-lg border border-ink-700 bg-ink-850 px-3.5 py-2 text-sm text-slate-200 hover:border-ink-600 hover:text-white disabled:opacity-50"
          >
            {busy ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  )
}

function Select({
  value, onChange, options, allLabel, wide,
}: { value: string; onChange: (v: string) => void; options: string[]; allLabel: string; wide?: boolean }) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`${wide ? 'w-44' : 'w-32'} rounded-md border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-slate-200 focus:border-emerald-500/60 focus:outline-none`}
    >
      <option value="">{allLabel}</option>
      {options.map((option) => (
        <option key={option} value={option}>{option}</option>
      ))}
    </select>
  )
}
