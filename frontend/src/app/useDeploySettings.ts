/**
 * @fileoverview 部署设置数据拉取与编辑队列管理 Hook。
 */

import { useEffect, useState, useSyncExternalStore } from 'react'
import { api } from '../api/client'
import type { Settings as SettingsData } from '../api/types'
import { editor } from '../config/editors'
import { useConnection } from './context'

/**
 * 获取部署设置并管理其编辑队列。
 *
 * @param pollRemote 是否根据远程地址状态轮询更新。
 * @returns 设置数据、加载错误、编辑快照与共享队列。
 */
export function useDeploySettings() {
  const [data, setData] = useState<SettingsData>()
  const [error, setError] = useState('')
  const connection = useConnection()
  const queue = editor('deploy')
  const edits = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot)

  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    const load = () => void api.request('settings.get', {})
      .then(data => {
        if (active) {
          setData(data)
          setError('')
        }
      })
      .catch(error => {
        if (active) {
          setError(error.message)
        }
      })
    load()
    return () => { active = false }
  }, [connection, queue])

  return {data, error, edits, queue}
}
