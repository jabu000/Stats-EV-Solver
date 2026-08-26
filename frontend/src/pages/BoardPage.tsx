import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchBoard, fetchMarkets } from '../lib/api'
import type { BoardResponse, League, MarketOption, Mode, PricedBet } from '../lib/types'
import { BetTypeBar } from '../components/BetTypeBar'
import { BetTable } from '../components/BetTable'
import { FilterBar, type FilterState } from '../components/FilterBar'
import { WhyDrawer } from '../components/WhyDrawer'

const EMPTY: FilterState = { team: '', game: '', position: '', search: '', minEdge: 0, minProbability: 0 }

interface Props {
  league: League
  slip: PricedBet[]
  onToggleSlip: (bet: PricedBet) => void
}

export function BoardPage({ league, slip, onToggleSlip }: Props) {
  const [mode, setMode] = useState<Mode>('value')
  const [market, setMarket] = useState<string | null>(null)
  const [filters, setFilters] = useState<FilterState>(EMPTY)
  const [markets, setMarkets] = useState<MarketOption[]>([])
  const [board, setBoard] = useState<BoardResponse | null>(null)
  const [selected, setSelected] = useState<PricedBet | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { fetchMarkets(league).then(setMarkets).catch(() => setMarkets([])) }, [league])

  useEffect(() => {
    setMarket(null)
    setFilters(EMPTY)
    // Drop the previous league's board immediately. Without this the table keeps
    // rendering the old sport's bets until the new fetch lands — so the NFL tab briefly
    // shows baseball, and a click on "+ Slip" in that window adds the wrong bet.
    setBoard(null)
  }, [league])

  // Monotonically increasing id for each request, so a slow earlier response can never
  // overwrite a newer one — switching tabs quickly used to be able to do exactly that.
  const requestId = useRef(0)

  const load = useCallback(() => {
    const id = ++requestId.current
    setBusy(true)
    // The board is fetched unfiltered by bet type so the counts on the bet-type row
    // stay meaningful; the rest of the filtering happens server-side.
    fetchBoard(league, {
      mode,
      team: filters.team || undefined,
      game: filters.game || undefined,
      position: filters.position || undefined,
      search: filters.search || undefined,
      minEdge: filters.minEdge || undefined,
      minProbability: filters.minProbability || undefined,
    })
      .then((response) => {
        if (id !== requestId.current) return
        setBoard(response)
        setError(null)
      })
      .catch((err) => { if (id === requestId.current) setError(String(err.message ?? err)) })
      .finally(() => { if (id === requestId.current) setBusy(false) })
  }, [league, mode, filters])

  useEffect(() => { load() }, [load])

  const counts = useMemo(() => {
    const result: Record<string, number> = {}
    for (const bet of board?.bets ?? []) result[bet.market] = (result[bet.market] ?? 0) + 1
    return result
  }, [board])

  const visible = useMemo(
    () => (board?.bets ?? []).filter((bet) => !market || bet.market === market),
    [board, market],
  )

  return (
    <div className="pb-40">
      <BetTypeBar markets={markets} active={market} counts={counts} onChange={setMarket} />
      <FilterBar
        filters={board?.filters ?? { teams: [], games: [], positions: [], markets: [] }}
        state={filters} mode={mode} onState={setFilters} onMode={setMode}
        onRefresh={load} busy={busy}
      />

      <div className="mx-auto max-w-[1600px] px-4 pt-3">
        <p className="text-xs text-slate-500">
          {mode === 'value' ? (
            <>
              Ranked by <strong className="text-emerald-400/90">Score</strong> — the edge over
              break-even, shrunk toward zero by how much of the projection came from the player&apos;s
              own data rather than a prior. A thin-sample outlier cannot top the board.
            </>
          ) : (
            <>
              Ranked by <strong className="text-emerald-400/90">win probability</strong>.
              Negative-EV bets are pushed to the bottom: likely to hit and worth betting are
              different questions.
            </>
          )}
        </p>
      </div>

      {board && (board.notes.length > 0 || board.unmapped_count > 0 || board.source !== 'live') && (
        <div className="mx-auto max-w-[1600px] space-y-1.5 px-4 pt-3">
          {board.source !== 'live' && (
            <Banner tone="info">
              Showing <strong>{board.source}</strong> data. Switch <code>DATA_MODE</code> to
              <code> live</code> and run “Test connections” in Settings to pull the real slate.
            </Banner>
          )}
          {board.unmapped_count > 0 && (
            <Banner tone="warn">
              {board.unmapped_count} player {board.unmapped_count === 1 ? 'name' : 'names'} could not be
              matched to stats and {board.unmapped_count === 1 ? 'is' : 'are'} excluded — see Settings.
            </Banner>
          )}
          {board.notes.map((note) => <Banner key={note} tone="warn">{note}</Banner>)}
        </div>
      )}

      {error && (
        <div className="mx-auto max-w-[1600px] px-4 pt-3">
          <Banner tone="error">{error}</Banner>
        </div>
      )}

      {!board ? (
        <div className="py-20 text-center text-slate-500">
          {error ? 'Could not build the board.' : `Building the ${league} board…`}
        </div>
      ) : (
        <BetTable
          bets={visible} mode={mode} slip={slip}
          onSelect={setSelected} onToggleSlip={onToggleSlip}
        />
      )}

      <WhyDrawer bet={selected} onClose={() => setSelected(null)} />
    </div>
  )
}

function Banner({ tone, children }: { tone: 'info' | 'warn' | 'error'; children: React.ReactNode }) {
  const styles = {
    info: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
    warn: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
    error: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
  }[tone]
  return <div className={`rounded-lg border px-3 py-2 text-xs ${styles}`}>{children}</div>
}
