import {useEffect, useId, useMemo, useRef, useState} from 'react'
import {api} from '../api/client'
import type {DockCard, DockReport} from '../api/types'
import {useApp, useConnection} from '../app/context'
import {useDraftInput} from '../components/FormControls'
import {ErrorBox, Loading} from '../components/ui'
import type {MindCatalog} from '../mind/types'
import './fleet-ledger.css'

const rarities = ['', 'N', 'R', 'SR', 'SSR', 'UR'] as const
type Rarity = typeof rarities[number]
type Draft = {revision: string; rows: DockCard[]; deleted: number[]}
const columns = [
  ['id', 'id'], ['name', 'name'], ['group_type', 'group'], ['level', 'level'],
  ['rarity', 'rarity'], ['base_rarity', 'baseRarity'], ['extra_exp', 'extraExp'],
  ['review', 'reviewFlag'], ['excluded', 'excluded'],
] as const
type SortField = typeof columns[number][0]
type Sort = {field: SortField; direction: 1 | -1}

function ShipName({value, label, list, placeholder, disabled, onCommit}: {
  value: string; label: string; list: string; placeholder: string; disabled: boolean; onCommit: (name: string) => void
}) {
  // 名称提交后才参与排序，避免打字时换行或翻页而丢失焦点。
  const draft = useDraftInput(value, text => onCommit(text.trim()), true)
  return <input {...draft} list={list} maxLength={100} aria-label={label} placeholder={placeholder} disabled={disabled}/>
}

function ShipNumber({value, label, disabled, max, nullable = false, onCommit}: {
  value: number | null; label: string; disabled: boolean; max?: number; nullable?: boolean; onCommit: (value: number | null) => void
}) {
  const draft = useDraftInput(value === null ? '' : String(value), text => onCommit(nullable && text === '' ? null : Number(text)))
  return <input {...draft} type="number" min={0} max={max} step={1} placeholder={nullable ? 'NULL' : undefined} aria-label={label} disabled={disabled}/>
}

export function FleetLedger({instance, report, onReport, onBusy, onDirty, locked = false}: {
  instance: string; report?: DockReport; locked?: boolean; onReport: (report: DockReport) => void
  onBusy: (busy: boolean) => void; onDirty: (dirty: boolean) => void
}) {
  const {language, ui, notify} = useApp(), connection = useConnection()
  const catalogId = useId()
  const [catalog, setCatalog] = useState<MindCatalog>(), [catalogError, setCatalogError] = useState('')
  const [catalogAttempt, setCatalogAttempt] = useState(0), [sort, setSort] = useState<Sort>()
  const [draft, setDraft] = useState<Draft>(), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [targets, setTargets] = useState<Record<string, string>>({})
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [page, setPage] = useState(0)
  const key = `azurpilot.fleet-ledger.${instance}`, scope = useRef({instance, token: 0}), writing = useRef(false)
  if (scope.current.instance !== instance) scope.current = {instance, token: scope.current.token + 1}
  useEffect(() => {
    let restored: Draft | undefined
    try {
      const value = JSON.parse(sessionStorage.getItem(key) ?? 'null')
      if (value && typeof value.revision === 'string' && Array.isArray(value.rows) && Array.isArray(value.deleted)) restored = value
    } catch { /* A damaged browser draft must not replace saved data. */ }
    setTargets({}); setDraft(restored); onDirty(!!restored); setError(''); setBusy(false); writing.current = false; setPage(0)
    return () => {scope.current.token++}
  }, [instance]) // Connection changes deliberately preserve the draft.
  useEffect(() => {setTargets({})}, [report?.pending_revision])
  useEffect(() => {setCatalog(undefined); setCatalogError(''); setSort(undefined)}, [instance])
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    setCatalogError('')
    void api.request('mind.catalog', {instance}).then(value => {
      if (active) setCatalog(value)
    }).catch(error => {if (active) setCatalogError((error as Error).message)})
    return () => {active = false}
  }, [instance, connection, catalogAttempt])
  function remember(value?: Draft) {
    setDraft(value); onDirty(!!value)
    try {if (value) sessionStorage.setItem(key, JSON.stringify(value)); else sessionStorage.removeItem(key)}
    catch { /* Editing remains available when browser storage is disabled. */ }
  }
  function change(id: number, update: Partial<DockCard>) {
    if (!report) return
    const value = draft ?? {revision: report.revision, rows: report.ships.map(row => ({...row})), deleted: []}
    remember({...value, rows: value.rows.map(row => row.id === id ? {...row, ...update} : row)})
  }
  function rename(row: DockCard, name: string) {
    // 目录名称已区分同名舰；不以原始别名或第一个模糊匹配猜测群组。
    const matches = catalog?.ships.filter(ship => ship.name === name && ship.group_type !== undefined) ?? []
    const ship = matches.length === 1 ? matches[0] : undefined
    if (ship && ship.group_type !== undefined) {
      if (row.name !== ship.name || row.group_type !== ship.group_type || row.rarity !== ship.rarity || row.base_rarity !== ship.base_rarity)
        change(row.id, {name: ship.name, group_type: ship.group_type, rarity: ship.rarity, base_rarity: ship.base_rarity})
    } else if (name !== row.name) change(row.id, {name, group_type: null, review: true})
  }
  function add() {
    if (!report) return
    const value = draft ?? {revision: report.revision, rows: report.ships.map(row => ({...row})), deleted: []}
    const id = Math.min(0, ...value.rows.map(row => row.id)) - 1
    remember({...value, rows: [...value.rows, {id, name: '', group_type: null, level: 1, rarity: 'N', base_rarity: 'N', extra_exp: null, review: false, excluded: false, source: 'manual'}]})
    setQuery(''); setFilter('all'); setSort(undefined); setPage(Math.floor(value.rows.length / 50))
  }
  function remove(id: number) {
    if (!report) return
    const value = draft ?? {revision: report.revision, rows: report.ships.map(row => ({...row})), deleted: []}
    remember({...value, deleted: value.deleted.includes(id) ? value.deleted.filter(item => item !== id) : [...value.deleted, id]})
  }
  const stale = !!draft && !!report && draft.revision !== report.revision
  async function save() {
    if (!draft || !report || locked || writing.current || stale || connection !== 'ready') return
    const rows = draft.rows.filter(row => !draft.deleted.includes(row.id))
    if (rows.some(row => !row.name.trim() || !Number.isInteger(row.level) || row.level < 0 || row.level > 125
      || row.group_type !== null && (!Number.isInteger(row.group_type) || row.group_type <= 0)
      || !(rarities as readonly string[]).includes(row.rarity)
      || !(rarities as readonly string[]).includes(row.base_rarity)
      || row.extra_exp !== null && (!Number.isSafeInteger(row.extra_exp) || row.extra_exp < 0))) {setError(ui('analysis.invalid')); return}
    const changes = [...rows.filter(row => row.id < 0 || JSON.stringify(row) !== JSON.stringify(report.ships.find(saved => saved.id === row.id)))
      .map(row => ({...row, id: row.id < 0 ? undefined : row.id, rarity: row.rarity as Rarity, base_rarity: row.base_rarity as Rarity})),
      ...draft.deleted.filter(id => id > 0).map(id => ({id, delete: true}))]
    if (!changes.length) {remember(); return}
    const token = scope.current.token, submitted = JSON.stringify(draft)
    writing.current = true; setBusy(true); onBusy(true); setError('')
    try {
      const value = await api.request('dock.patch', {instance, revision: draft.revision, changes})
      try {if (sessionStorage.getItem(key) === submitted) sessionStorage.removeItem(key)} catch { /* Saved remotely. */ }
      if (scope.current.token !== token) return
      remember(); onReport(value); notify(ui('analysis.saved'))
    } catch (e) {if (scope.current.token === token) setError((e as Error).message)}
    finally {if (scope.current.token === token) {writing.current = false; setBusy(false); onBusy(false)}}
  }
  async function review(accept: boolean, ids?: string[]) {
    if (!report || draft || locked || writing.current || connection !== 'ready' || accept && report.pending_changes.some(item => (!ids || ids.includes(item.id)) && item.operation === 'resolve' && !targets[item.id])) return
    const token = scope.current.token
    writing.current = true; setBusy(true); onBusy(true); setError('')
    try {
      const value = await api.request('dock.review', {instance, revision: report.revision, pending_revision: report.pending_revision!, decisions: report.pending_changes.filter(item => !ids || ids.includes(item.id)).map(item => ({id: item.id, accept, ...(accept && item.operation === 'resolve' ? {ship_id: targets[item.id] === 'new' ? null : Number(targets[item.id])} : {})}))})
      if (scope.current.token === token) onReport(value)
    } catch (e) {if (scope.current.token === token) setError((e as Error).message)}
    finally {if (scope.current.token === token) {writing.current = false; setBusy(false); onBusy(false)}}
  }
  const collator = useMemo(() => new Intl.Collator(language, {numeric: true, sensitivity: 'base'}), [language])
  const rows = useMemo(() => {
    const result = (draft?.rows ?? report?.ships ?? []).filter(row => `${row.name} ${row.id} ${row.group_type ?? ''}`.toLowerCase().includes(query.toLowerCase())
      && (filter === 'unknown' ? row.extra_exp === null : filter === 'review' ? row.review : true))
    if (sort) result.sort((left, right) => {
      const a = left[sort.field], b = right[sort.field]
      // 未知值始终排在已知值后，额外经验 0 不等于未知。
      if (a === null || a === '') return b === null || b === '' ? left.id - right.id : 1
      if (b === null || b === '') return -1
      const comparison = sort.field === 'rarity' || sort.field === 'base_rarity'
        ? rarities.indexOf(a as Rarity) - rarities.indexOf(b as Rarity)
        : typeof a === 'string' && typeof b === 'string' ? collator.compare(a, b) : Number(a) - Number(b)
      return comparison * sort.direction || left.id - right.id
    })
    return result
  }, [draft?.rows, report?.ships, query, filter, sort, collator])
  const pages = Math.max(1, Math.ceil(rows.length / 50)), currentPage = Math.min(page, pages - 1)
  const disabled = busy || locked || connection !== 'ready' || !report
  const rowLabel = (label: string, row: DockCard) => `${label} · ${row.name || ui('analysis.new')} #${row.id}`
  const fieldLabels = {
    id: 'analysis.id', group_type: 'analysis.group', name: 'analysis.name', level: 'analysis.level',
    rarity: 'analysis.rarity', base_rarity: 'analysis.baseRarity', extra_exp: 'analysis.extraExp',
    excluded: 'analysis.excluded', review: 'analysis.reviewFlag', source: 'analysis.sourceMetadata',
    observed_level: 'analysis.observedLevel', owned: 'analysis.owned', full_star: 'analysis.fullStar',
    lv120: 'analysis.lv120', level_toggle: 'analysis.levelToggle', low: 'analysis.low', high: 'analysis.high',
    manual: 'analysis.manual',
  } as const
  const fieldLabel = (field: string) => field in fieldLabels ? ui(fieldLabels[field as keyof typeof fieldLabels]) : field
  function displayValue(value: unknown): string {
    if (value === null || value === undefined || value === '') return ui('analysis.unknownValue')
    if (typeof value === 'boolean') return ui(value ? 'analysis.yes' : 'analysis.no')
    if (Array.isArray(value)) return value.map(displayValue).join(', ')
    if (typeof value === 'object') return Object.entries(value).map(([field, item]) => `${fieldLabel(field)}: ${displayValue(item)}`).join(' / ')
    return String(value)
  }
  const cardText = (row: Record<string, unknown> | null) => row ? displayValue(row) : '—'
  const kinds = {ship: 'analysis.shipKind', technology: 'analysis.technologyKind', nation: 'analysis.nationKind'} as const
  const operations = {add: 'analysis.operationAdd', update: 'analysis.operationUpdate', remove: 'analysis.operationRemove', resolve: 'analysis.operationResolve'} as const
  return <div className="fleet-ledger">
    <p>{ui('analysis.ledgerHint')}</p>
    <p className="muted">{ui('analysis.editHint')}</p>
    {catalogError && <ErrorBox message={catalogError} retry={() => setCatalogAttempt(value => value + 1)}/>}
    {!catalog && !catalogError && <p role="status">{ui('analysis.catalogLoading')}</p>}
    {error && <ErrorBox message={error}/>}
    {!report && <Loading/>}
    {draft && <p role="status">{ui(stale ? 'analysis.stale' : 'analysis.dirty')}</p>}
    <div className="mind-toolbar">
      <label>{ui('analysis.search')}<input type="search" value={query} onChange={e => {setQuery(e.target.value); setPage(0)}}/></label>
      <label>{ui('analysis.filter')}<select value={filter} onChange={e => {setFilter(e.target.value); setPage(0)}}>
        <option value="all">{ui('analysis.all')}</option><option value="unknown">{ui('analysis.unknown')}</option><option value="review">{ui('analysis.needsReview')}</option>
      </select></label>
      <button className="button secondary" disabled={disabled} onClick={add}>{ui('analysis.add')}</button>
      <button className="button primary" disabled={disabled || !draft || stale} onClick={() => void save()}>{ui('analysis.save')}</button>
      <button className="button secondary" disabled={busy || !draft} onClick={() => {remember(); setError('')}}>{ui('analysis.discard')}</button>
    </div>
    <datalist id={catalogId}>{catalog?.ships.map(ship => <option key={`${ship.group_type}:${ship.name}`} value={ship.name}>{ship.rarity}</option>)}</datalist>
    <div className="ledger-table-wrap"><table><thead><tr>{columns.map(([field, label]) => <th key={field} scope="col" aria-sort={sort?.field === field ? sort.direction === 1 ? 'ascending' : 'descending' : 'none'}>
      <button type="button" className="ledger-sort" onClick={() => {setSort({field, direction: sort?.field === field && sort.direction === 1 ? -1 : 1}); setPage(0)}}>
        {ui(`analysis.${label}`)} <span aria-hidden="true">{sort?.field === field ? sort.direction === 1 ? '↑' : '↓' : '↕'}</span>
      </button>
    </th>)}<th scope="col">{ui('analysis.sourceMetadata')}</th><th scope="col">{ui('analysis.delete')}</th></tr></thead>
      <tbody>{rows.slice(currentPage * 50, (currentPage + 1) * 50).map(row => <tr key={row.id} className={draft?.deleted.includes(row.id) ? 'ledger-deleted' : ''}>
        <td>{row.id < 0 ? `${ui('analysis.new')} ${-row.id}` : row.id}</td>
        <td><ShipName value={row.name} label={rowLabel(ui('analysis.name'), row)} list={catalogId} placeholder={ui('analysis.chooseShip')} disabled={disabled || !catalog} onCommit={name => rename(row, name)}/></td>
        <td><span title={ui('analysis.groupAuto')}>{row.group_type ?? '—'}</span></td>
        <td><ShipNumber value={row.level} max={125} label={rowLabel(ui('analysis.level'), row)} disabled={disabled} onCommit={level => change(row.id, {level: level ?? 0})}/></td>
        {(['rarity', 'base_rarity'] as const).map(field => <td key={field}><select aria-label={rowLabel(ui(field === 'rarity' ? 'analysis.rarity' : 'analysis.baseRarity'), row)} value={row[field]} disabled={disabled} onChange={e => change(row.id, {[field]: e.target.value})}>
          {rarities.map(value => <option key={value} value={value}>{value || '—'}</option>)}
        </select></td>)}
        <td><ShipNumber value={row.extra_exp} nullable label={rowLabel(ui('analysis.extraExp'), row)} disabled={disabled} onCommit={extra_exp => change(row.id, {extra_exp})}/></td>
        <td><input type="checkbox" aria-label={rowLabel(ui('analysis.reviewFlag'), row)} checked={row.review} disabled={disabled} onChange={e => change(row.id, {review: e.target.checked})}/></td>
        <td><input type="checkbox" aria-label={rowLabel(ui('analysis.excluded'), row)} checked={row.excluded} disabled={disabled} onChange={e => change(row.id, {excluded: e.target.checked})}/></td>
        <td>{row.source}</td><td><button className="button secondary" aria-label={rowLabel(ui(draft?.deleted.includes(row.id) ? 'analysis.restore' : 'analysis.delete'), row)} disabled={disabled} onClick={() => remove(row.id)}>{ui(draft?.deleted.includes(row.id) ? 'analysis.restore' : 'analysis.delete')}</button></td>
      </tr>)}</tbody></table></div>
    {!rows.length && <p>{ui('analysis.emptyLedger')}</p>}
    <div className="mind-toolbar"><button className="button secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{ui('analysis.previous')}</button><span>{ui('analysis.page')} {currentPage + 1} / {pages} · {rows.length}</span><button className="button secondary" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{ui('analysis.next')}</button></div>
    <section className="mind-panel" aria-label={ui('analysis.pending')}>
      <h2>{ui('analysis.pending')}</h2><p>{ui('analysis.pendingHint')}</p>
      <div className="mind-toolbar"><button className="button primary" disabled={disabled || !!draft || !report?.pending_changes.length || report.pending_changes.some(item => item.operation === 'resolve' && !targets[item.id])} onClick={() => void review(true)}>{ui('analysis.acceptAll')}</button><button className="button secondary" disabled={disabled || !!draft || !report?.pending_changes.length} onClick={() => void review(false)}>{ui('analysis.rejectAll')}</button></div>
      {!report?.pending_changes.length && <p>{ui('analysis.noPending')}</p>}
      {report?.pending_changes.map(item => <article key={item.id} className="ledger-difference">
        <p><strong>{item.name || item.kind}</strong> · {ui(kinds[item.kind])} / {ui(operations[item.operation])} · #{item.id}</p>
        <p>{ui('analysis.before')}: {cardText(item.before)}</p><p>{ui('analysis.after')}: {cardText(item.after)}</p>
        {item.operation === 'resolve' && <label>{ui('analysis.target')}<select aria-label={`${ui('analysis.target')} ${item.id}`} value={targets[item.id] ?? ''} disabled={disabled || !!draft} onChange={e => setTargets(previous => ({...previous, [item.id]: e.target.value}))}>
          <option value="">{ui('analysis.chooseTarget')}</option><option value="new">{ui('analysis.addAsNew')}</option>
          {item.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>#{candidate.id} {candidate.name} · Lv.{candidate.level}</option>)}
        </select></label>}
        <button className="button primary" disabled={disabled || !!draft || item.operation === 'resolve' && !targets[item.id]} aria-label={`${ui('analysis.accept')} ${item.id}`} onClick={() => void review(true, [item.id])}>{ui('analysis.accept')}</button>{' '}
        <button className="button secondary" disabled={disabled || !!draft} aria-label={`${ui('analysis.reject')} ${item.id}`} onClick={() => void review(false, [item.id])}>{ui('analysis.reject')}</button>
      </article>)}
    </section>
  </div>
}
