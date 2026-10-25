import {useEffect, useState} from 'react'
import {useParams, useSearchParams} from 'react-router-dom'
import {api} from '../api/client'
import type {DockReport} from '../api/types'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading, PageTitle} from '../components/ui'
import {MindCalculator} from './MindCalculator'
import {FleetTechnology} from './FleetTechnology'
import '../mind/mind.css'

export function FleetAnalysis() {
  const {instance = ''} = useParams(), [params, setParams] = useSearchParams()
  const {ui, instances, notify} = useApp(), connection = useConnection()
  const [report, setReport] = useState<DockReport>(), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [min, setMin] = useState('1'), [max, setMax] = useState('125')
  const tab = params.get('tab') === 'technology' ? 'technology' : 'mind'
  const current = instances.find(item => item.name === instance)
  const scanning = current?.status === 'running' && ['DockScan', 'dock_scan'].includes(current.currentTask ?? '')
  const reason = connection !== 'ready' ? ui('mind.scanDisconnected') : !current || !report ? ui('mind.scanLoading')
    : current.status === 'running' ? ui(scanning ? 'mind.scanInProgress' : 'mind.scanRunning')
    : current.status === 'updating' ? ui('mind.scanUpdating') : current.region && current.region !== 'cn' ? ui('mind.scanUnsupported') : ''
  useEffect(() => {
    let active = true
    setReport(undefined); setError('')
    if (connection !== 'ready') return
    const load = async (initial = false) => {
      try {
        const value = await api.request('dock.report', {instance})
        if (active) {
          setReport(value)
          if (initial) {setMin(String(value.min_level)); setMax(String(value.max_level))}
        }
      } catch (e) {if (active) setError((e as Error).message)}
    }
    void load(true)
    const timer = setInterval(() => void load(), 5000)
    return () => {active = false; clearInterval(timer)}
  }, [instance, connection])
  async function run(stop = false) {
    setBusy(true); setError('')
    try {
      if (stop) await api.request('scheduler.stop', {instance})
      else {
        const low = Number(min), high = Number(max)
        if (!min.trim() || !max.trim() || !Number.isInteger(low) || !Number.isInteger(high) || low < 1 || high > 125 || low > high) throw new Error(ui('mind.invalidRange'))
        setReport(await api.request('dock.configure', {instance, min_level: low, max_level: high}))
        await api.request('tasks.run', {instance, task: 'DockScan'})
        notify(ui('mind.scanStarted'))
      }
    } catch (e) {setError((e as Error).message)} finally {setBusy(false)}
  }
  const range = report?.scan_range ?? report
  const sourceKey = report?.source === 'legacy_mind' ? 'analysis.legacy_mind' : report?.source === 'legacy_tech' ? 'analysis.legacy_tech' : report?.source === 'scan' ? 'analysis.source_scan' : 'analysis.source_empty'
  return <div className="mind-calculator">
    <PageTitle title={ui('analysis.title')}/><p>{ui('analysis.hint')}</p>
    <section className="mind-panel" aria-label={ui('analysis.source')}>
      <h2>{ui('analysis.source')}</h2>
      {error && <ErrorBox message={error}/>}
      {!report && !error && <Loading/>}
      {report && <p role="status">{ui('analysis.origin')}: {ui(sourceKey)} · {ui('analysis.time')}: {report.updated_at?.replace('T', ' ') || ui('analysis.empty')} · {ui('analysis.range')}: Lv.{range?.min_level}–{range?.max_level} · {ui('analysis.count')}: {report.ships.length} · {ui('analysis.review')}: {report.review_count} · {ui(report.complete ? 'analysis.complete' : 'analysis.partial')}</p>}
      <div className="mind-toolbar">
        <label>{ui('mind.minLevel')}<input type="number" min={1} max={125} value={min} disabled={busy || !!reason} onChange={e => setMin(e.target.value)}/></label>
        <label>{ui('mind.maxLevel')}<input type="number" min={1} max={125} value={max} disabled={busy || !!reason} onChange={e => setMax(e.target.value)}/></label>
        <button className="button primary" title={reason} disabled={busy || !!reason} onClick={() => void run()}>{ui(scanning ? 'mind.scanning' : 'analysis.scan')}</button>
        {scanning && <button className="button secondary" disabled={busy || connection !== 'ready'} onClick={() => void run(true)}>{ui('mind.stop')}</button>}
        {reason && <p role="status">{reason}</p>}
      </div><p className="muted">{ui('analysis.scanHint')}</p>
    </section>
    <div className="ft-chips" role="tablist" aria-label={ui('analysis.title')}>
      {(['mind', 'technology'] as const).map(value => <button key={value} role="tab" aria-selected={tab === value} aria-controls={`analysis-${value}`} id={`tab-${value}`} onClick={() => setParams({tab: value}, {replace: true})}>{ui(value === 'mind' ? 'analysis.mind' : 'analysis.technology')}</button>)}
    </div>
    <section role="tabpanel" id="analysis-mind" aria-labelledby="tab-mind" hidden={tab !== 'mind'}><MindCalculator key={instance}/></section>
    <section role="tabpanel" id="analysis-technology" aria-labelledby="tab-technology" hidden={tab !== 'technology'}><FleetTechnology key={instance}/></section>
  </div>
}
