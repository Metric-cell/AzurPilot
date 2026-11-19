/**
 * @fileoverview 调度器核心控制卡片与任务数量摘要组件。
 */

import { CalendarClock, Play, Square } from 'lucide-react'
import { useApp } from '../app/context'
import { useSchedulerControls, type SchedulerPanelProps } from './useSchedulerControls'

/** 调度器面板 Props 类型，供 useSchedulerControls 消费方复用。 */
export type { SchedulerPanelProps } from './useSchedulerControls'

/**
 * 调度器卡片：状态、三类任务计数与启停按钮。
 *
 * 现代主题的总览页左列、其余主题的右栏、实例设置页的调度器栏都渲染它，
 * 三处共用同一份启停逻辑，保证「不带旧配置启动调度器」的排队屏障不会在某一处漏掉。
 */
export function SchedulerWidget(props: SchedulerPanelProps) {
  const {disabled, busy, isRunning, running, pending, waiting, toggleScheduler} = useSchedulerControls(props)
  const {ui} = useApp()

  return <section className={'scheduler-widget'} data-apu-component="scheduler" data-state={isRunning ? 'running' : 'stopped'} aria-label={ui('scheduler.title')}>
    <div className={'scheduler-widget-heading'} data-apu-part="header">
      <div><CalendarClock size={18} strokeWidth={1.8}/><span>{ui('scheduler.title')}</span></div>
    </div>
    <div className={'scheduler-stats'} data-apu-part="counts">
      <div className={'running'} data-apu-part="running"><strong>{running}</strong><span>{ui('scheduler.running')}</span></div>
      <div className={'pending'} data-apu-part="pending"><strong>{pending}</strong><span>{ui('scheduler.pending')}</span></div>
      <div className={'waiting'} data-apu-part="waiting"><strong>{waiting}</strong><span>{ui('scheduler.waiting')}</span></div>
    </div>
    <button
      type="button"
      className={`button scheduler-toggle ${isRunning ? 'danger' : 'primary'}`}
      data-apu-part="toggle"
      onClick={toggleScheduler}
      disabled={disabled}
    >
      {isRunning ? <Square size={11} fill="currentColor" strokeWidth={0}/> : <Play size={14}/>} {busy ? ui('scheduler.processing') : isRunning ? ui('scheduler.stop') : ui('scheduler.start')}
    </button>
  </section>
}
