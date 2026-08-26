import type { MarketOption } from '../lib/types'

interface Props {
  markets: MarketOption[]
  active: string | null
  counts: Record<string, number>
  onChange: (market: string | null) => void
}

/** The bet-type row that sits directly under the tabs. */
export function BetTypeBar({ markets, active, counts, onChange }: Props) {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0)
  return (
    <div className="border-b border-ink-800 bg-ink-900/60">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-2 px-4 py-3">
        <Chip label="All bet types" count={total} active={active === null} onClick={() => onChange(null)} />
        {markets.map((market) => (
          <Chip
            key={market.value}
            label={market.label}
            count={counts[market.value] ?? 0}
            active={active === market.value}
            onClick={() => onChange(market.value)}
          />
        ))}
      </div>
    </div>
  )
}

function Chip({
  label, count, active, onClick,
}: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm transition-colors ${
        active
          ? 'border-emerald-400/60 bg-emerald-400/10 text-emerald-200'
          : 'border-ink-700 bg-ink-850 text-slate-300 hover:border-ink-600 hover:text-white'
      }`}
    >
      {label}
      <span className={`tabular text-xs ${active ? 'text-emerald-300/80' : 'text-slate-500'}`}>
        {count}
      </span>
    </button>
  )
}
