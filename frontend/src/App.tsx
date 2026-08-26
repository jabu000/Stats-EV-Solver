import { useState } from 'react'
import { TabBar, type Tab } from './components/TabBar'
import { BoardPage } from './pages/BoardPage'
import { TrackRecordPage } from './pages/TrackRecordPage'
import { SettingsPage } from './pages/SettingsPage'
import { EntryBuilder } from './components/EntryBuilder'
import type { PricedBet } from './lib/types'

export default function App() {
  const [tab, setTab] = useState<Tab>('MLB')
  // The slip survives tab changes on purpose: cross-sport entries are allowed at
  // Underdog, and losing a half-built slip on a tab switch would be maddening.
  const [slip, setSlip] = useState<PricedBet[]>([])

  const toggleSlip = (bet: PricedBet) =>
    setSlip((current) =>
      current.some((entry) => entry.id === bet.id)
        ? current.filter((entry) => entry.id !== bet.id)
        : [...current, bet],
    )

  return (
    <div className="min-h-screen bg-ink-950">
      <TabBar active={tab} onChange={setTab} />

      {tab === 'track' ? (
        <TrackRecordPage />
      ) : tab === 'settings' ? (
        <SettingsPage />
      ) : (
        <BoardPage league={tab} slip={slip} onToggleSlip={toggleSlip} />
      )}

      <EntryBuilder
        slip={slip}
        onRemove={(bet) => setSlip((current) => current.filter((entry) => entry.id !== bet.id))}
        onClear={() => setSlip([])}
      />
    </div>
  )
}
