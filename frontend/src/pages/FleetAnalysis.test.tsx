import {describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {translateUi} from '../i18n'
import {api} from '../api/client'
import {FleetAnalysis} from './FleetAnalysis'

vi.mock('../app/context', () => ({
  useConnection: () => 'ready',
  useApp: () => ({ui: (key: Parameters<typeof translateUi>[1]) => translateUi('zh-CN', key),
    instances: [{name: 'test', status: 'stopped', region: 'cn'}], notify: vi.fn()}),
}))
vi.mock('./MindCalculator', () => ({MindCalculator: () => <p>mind editor</p>}))
vi.mock('./FleetTechnology', () => ({FleetTechnology: () => <p>technology editor</p>}))

describe('舰队数据分析页面', () => {
  it.each(['mind', 'technology'])('使用统一扫描入口，%s 标签与两个编辑器保持独立', tab => {
    const request = vi.spyOn(api, 'request')
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={[`/i/test/task/FleetAnalysis?tab=${tab}`]}><Routes>
      <Route path="/i/:instance/task/FleetAnalysis" element={<FleetAnalysis/>}/>
    </Routes></MemoryRouter>)
    expect(html).toContain('共享船坞数据')
    expect(html.match(/>扫描船坞</g)).toHaveLength(1)
    expect(html).toContain(`id="tab-${tab}"`)
    expect(html).toContain(`aria-controls="analysis-${tab}"`)
    expect(html).toContain('mind editor')
    expect(html).toContain('technology editor')
    expect(html).toContain('不升级、突破、自动觉醒或更换舰船')
    expect(html).toContain('导入本实例旧舰船数据')
    expect(html).toContain('无需上传文件')
    expect(html).not.toContain('type="file"')
    expect(request).not.toHaveBeenCalled()
    request.mockRestore()
  })
})
