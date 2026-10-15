/** 启动器关闭偏好只通过原生桥读写，浏览器访问时不展示。 */
import {useEffect, useRef, useState} from 'react'
import {useApp} from '../app/context'
import {Select} from './FormControls'

type CloseBehavior = 'ask' | 'tray' | 'exit'
type Preferences = {closeBehavior: CloseBehavior; backgroundSupported: boolean}
type NativeBridge = {invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>}

function preferences(value: unknown): Preferences {
  const result = value as Partial<Preferences> | null
  if (!result || !['ask', 'tray', 'exit'].includes(result.closeBehavior ?? '') || typeof result.backgroundSupported !== 'boolean') {
    throw new Error('Invalid launcher preferences')
  }
  return result as Preferences
}

export function LauncherCloseBehavior() {
  const {ui} = useApp()
  const [value, setValue] = useState<Preferences | null>(null)
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle')
  const bridge = useRef<NativeBridge | null>(null)
  const generation = useRef(0)
  const busy = useRef(false)

  useEffect(() => {
    const current = ++generation.current
    const native = (window as Window & {__ALAS_NATIVE__?: NativeBridge}).__ALAS_NATIVE__
    if (native && typeof native.invoke === 'function') {
      bridge.current = native
      void Promise.resolve().then(() => native.invoke('get_launcher_preferences')).then(result => {
        const next = preferences(result)
        if (generation.current === current) setValue(next)
      }).catch(() => { /* 未建立可信原生连接时隐藏入口。 */ })
    }
    return () => { generation.current++ }
  }, [])

  async function save(behavior: CloseBehavior) {
    if (!value || !bridge.current || busy.current || behavior === value.closeBehavior) return
    if (behavior === 'tray' && !value.backgroundSupported) return
    const previous = value
    const current = generation.current
    busy.current = true
    setValue({...previous, closeBehavior: behavior})
    setStatus('saving')
    try {
      const next = preferences(await bridge.current.invoke('set_launcher_close_behavior', {behavior}))
      if (generation.current === current) {
        setValue(next)
        setStatus('saved')
      }
    } catch {
      if (generation.current === current) {
        setValue(previous)
        setStatus('failed')
      }
    } finally {
      busy.current = false
    }
  }

  if (!value) return null
  return <div className="field-row">
    <div className="field-label">
      <label htmlFor="launcher-close-behavior">{ui('launcher.closeBehavior')}</label>
      <p id="launcher-close-help">{ui(value.backgroundSupported ? 'launcher.closeHelp' : 'launcher.closeUnsupported')}</p>
      {status !== 'idle' && <p role={status === 'failed' ? 'alert' : 'status'}>{ui(
        status === 'saving' ? 'launcher.saving' : status === 'saved' ? 'launcher.saved' : 'launcher.saveFailed',
      )}</p>}
    </div>
    <div className="field-control">
      <Select id="launcher-close-behavior" value={value.closeBehavior} disabled={status === 'saving'}
        aria-describedby="launcher-close-help" onChange={event => { void save(event.target.value as CloseBehavior) }}>
        {value.backgroundSupported && <option value="ask">{ui('launcher.closeAsk')}</option>}
        {value.backgroundSupported && <option value="tray">{ui('launcher.closeTray')}</option>}
        <option value="exit">{ui('launcher.closeExit')}</option>
      </Select>
    </div>
  </div>
}
