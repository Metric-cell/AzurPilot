import { useApp } from '../app/context'
import { ErrorBox, Loading, PageTitle } from '../components/ui'
import { DeployGroups } from '../components/DeployGroups'
import { useDeploySettings } from '../app/useDeploySettings'

/** 系统级部署设置，包含本地 WebUI 配置；外观偏好在「界面设置」。 */
export function Settings() {
  const {data, error, edits, queue} = useDeploySettings()
  const {ui} = useApp()

  return (
    <>
      <PageTitle title={ui('nav.settings')} />
      {error && <ErrorBox message={error} />}
      {edits.storageError && <ErrorBox message={edits.storageError} />}
      {!data ? <Loading/> : (
        <DeployGroups data={data} edits={edits} queue={queue} />
      )}
    </>
  )
}
