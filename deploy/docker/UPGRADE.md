# Docker 本地源码运行与调试

默认使用本地源码挂载，不再区分生产和开发模式。在仓库根目录执行：

```bash
docker compose up -d ALAS
docker compose ps
```

整个仓库挂载到 `/app/AzurPilot`，包括源码、配置和日志。容器使用 Python 3.14.6、uv 0.11.32 和 Node 24；`.venv`、`frontend/node_modules`、`frontend/dist` 分别存放在三个 Docker 卷中，与宿主机依赖隔离。已有配置、登录密码和设备身份继续使用本地文件。

首次创建 Python 卷时复制镜像内的依赖，每次启动执行 `uv sync --frozen` 同步锁文件（含开发依赖）。需要新包时会联网下载；应用的外联裁剪范围仍遵循 [clean 策略](../../CLEAN_POLICY.md)。

## 清理宿主机依赖

仅使用 Docker 时，宿主机原有的 Python 依赖可以清理，但应先停止容器，清理后执行 `docker compose up -d --no-build --force-recreate ALAS` 恢复挂载。不要在容器运行时删除宿主机 `.venv` 目录本身：它也是嵌套依赖卷的挂载点，删除会让运行中的容器无法再访问卷内的 Python 和依赖，即使 Web 服务暂时还能响应。

容器重建后，宿主机可能出现一个仅占几 KB 的空 `.venv` 目录；保留这个挂载点即可，1.3 GB 的原宿主机依赖无需恢复。`frontend/node_modules` 和 `frontend/dist` 同样是嵌套卷挂载点，清理时也应先停止容器。

若删除目录后已经出现 `unhealthy`，并且健康检查记录为 `.venv/bin/python: no such file or directory`，使用上述重建命令恢复现有依赖卷，不需要重新构建镜像或删除卷。恢复后应同时检查 Python 依赖导入和 `/healthz`。

## 修改 Python

编辑本地文件后重启即可，无需重建镜像：

```bash
docker compose restart ALAS
docker compose logs -f --tail 100 ALAS
```

容器会立即看到文件修改，但已经加载模块的 Python 进程需要重启。重启也会中断当前游戏任务。

测试和交互调试直接使用容器环境：

```bash
docker compose exec ALAS .venv/bin/python -m unittest tests.test_clean_network_policy
docker compose exec ALAS bash
# 在容器 shell 中运行：
.venv/bin/python -m pdb 你的脚本.py
```

## 修改 React 前端

在另一个终端启动 Vite：

```bash
docker compose exec ALAS sh -ec 'cd frontend; npm ci --no-audit --no-fund; npm run dev -- --host 0.0.0.0'
```

浏览器打开 `http://服务器IP:5173`，本地修改后页面实时刷新。API 和 WebSocket 代理到 `22267` 的同一个后端，继续使用原登录密码。自定义后端端口时，在 `npm run dev` 前设置 `AZURPILOT_BACKEND=http://127.0.0.1:实际端口`。结束调试按 Ctrl+C。

需要在日常使用的 `22267` 页面看到静态前端更新时：

```bash
docker compose exec ALAS .venv/bin/python -m deploy.frontend
```

后端启动时也会检查前端源文件变化并自动构建；源码未变时复用卷中的产物。Vite 运行期间不要同时安装依赖或执行静态构建，以免打断开发服务器。

## 修改依赖或运行环境

新增 Python 依赖，在容器中更新本地项目与锁文件：

```bash
docker compose exec ALAS uv add 包名
docker compose restart ALAS
```

只有 Python 版本、系统库、Node 或 Dockerfile 等运行环境变化，才需要重建镜像并重建容器：

```bash
docker compose build
docker compose up -d --no-build ALAS
```

镜像默认名为 `alas:py314`，服务使用 host 网络，端口由 `ALAS_WEBUI_PORT` 指定，默认 `22267`。本地 `config/`、`log/` 等数据持续保留。升级 Python 时启动命令会按镜像解释器重新同步独立虚拟环境。

## 本机迁移记录

2026-09-19 将默认 Compose 改为源码挂载，取消额外开发 Compose 和切换脚本。源码合并基线为 `b84d53afe`，包含上游 dev `6e8b93c7b`。本次运行环境镜像标签为 `alas:source-20260919`，同时用于 `alas:py314`。

本次切换前的 Compose、配置及日志备份位于 `/home/dreamydust/alas_py314/backups/20260919-source-mounted-docker/`。此前升级的完整回滚资料位于 `/home/dreamydust/alas_py314/backups/20260919-clean-dev-docker/`。旧镜像和历史虚拟环境卷保留供回滚。

切换后容器为 `healthy`、重启次数为 0，首页及 `/healthz` 正常；已验证本地文件修改实时出现在容器中。4 份配置 JSON、密码和设备身份均与备份一致。镜像中 11 项 clean／前端构建检查通过（1 项 Windows 检查跳过），容器内 Python、uv、Node、npm 和 Ruff 可用。

### 发现的问题

仅运行镜像内源码不方便本地调试；另设开发模式会增加切换负担。直接挂载仓库又会遮蔽镜像中的依赖。

### 建议修正

统一默认 Compose 为源码挂载，三个独立卷保存容器依赖和前端产物，启动时同步锁文件，镜像提供 Node/npm 以支持前端调试。

### 是否需要继续修改

本次需求已完成，配置和运行状态验证通过，无需额外切换脚本。
