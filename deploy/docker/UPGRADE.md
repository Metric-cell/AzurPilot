# Docker 本地源码运行与调试

默认使用本地源码挂载，不再区分生产和开发模式。在仓库根目录执行：

```bash
docker compose up -d ALAS
docker compose ps
```

整个仓库挂载到 `/app/AzurPilot`，包括源码、配置和日志。容器使用 Python 3.14.6、uv 0.11.32 和 Node 24；`/opt/venv`、`frontend/node_modules`、`frontend/dist` 分别存放在三个 Docker 卷中，与宿主机依赖隔离。已有配置、登录密码和设备身份继续使用本地文件。

首次创建 Python 卷时复制镜像内的依赖，每次启动执行 `uv sync --frozen` 同步锁文件（含开发依赖）。需要新包时会联网下载；应用的外联裁剪范围仍遵循 [clean 策略](../../CLEAN_POLICY.md)。

## Python 环境与本地清理

容器通过 `UV_PROJECT_ENVIRONMENT=/opt/venv` 使用源码目录之外的 Python 环境，`PATH`、启动命令、健康检查和程序内依赖同步均使用该路径。宿主机 `.venv` 不再作为容器挂载点，也不会被容器加载，可以独立删除或重建；不需要保留空目录。

从旧配置迁移时，`docker compose build` 后执行 `docker compose up -d --no-build ALAS` 即可。已有 `alas-python` 卷会挂到 `/opt/venv`，启动时同步锁文件，不需要删除依赖卷。

前端 `frontend/node_modules` 和 `frontend/dist` 仍是源码目录内的嵌套卷挂载点；若要删除宿主机这两个目录，应先停止容器，清理后执行 `docker compose up -d --no-build --force-recreate ALAS` 恢复挂载。

## 修改 Python

编辑本地文件后重启即可，无需重建镜像：

```bash
docker compose restart ALAS
docker compose logs -f --tail 100 ALAS
```

容器会立即看到文件修改，但已经加载模块的 Python 进程需要重启。重启也会中断当前游戏任务。

测试和交互调试直接使用容器环境：

```bash
docker compose exec ALAS python -m unittest tests.test_clean_network_policy
docker compose exec ALAS bash
# 在容器 shell 中运行：
python -m pdb 你的脚本.py
```

## 修改 React 前端

在另一个终端启动 Vite：

```bash
docker compose exec ALAS sh -ec 'cd frontend; npm ci --no-audit --no-fund; npm run dev -- --host 0.0.0.0'
```

浏览器打开 `http://服务器IP:5173`，本地修改后页面实时刷新。API 和 WebSocket 代理到 `22267` 的同一个后端，继续使用原登录密码。自定义后端端口时，在 `npm run dev` 前设置 `AZURPILOT_BACKEND=http://127.0.0.1:实际端口`。结束调试按 Ctrl+C。

需要在日常使用的 `22267` 页面看到静态前端更新时：

```bash
docker compose exec ALAS python -m deploy.frontend
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

2026-09-19 将默认 Compose 改为源码挂载，取消额外开发 Compose 和切换脚本。源码合并基线为 `b84d53afe`，包含上游 dev `6e8b93c7b`。最初源码挂载镜像标签为 `alas:source-20260919`。后续将 Python 环境移到 `/opt/venv`，镜像标签为 `alas:opt-venv-20260919`，同时用于 `alas:py314`。

本次切换前的 Compose、配置及日志备份位于 `/home/dreamydust/alas_py314/backups/20260919-source-mounted-docker/`。此前升级的完整回滚资料位于 `/home/dreamydust/alas_py314/backups/20260919-clean-dev-docker/`。`/opt/venv` 迁移前的 Compose、配置、日志和验证记录位于 `/home/dreamydust/alas_py314/backups/20260919-opt-venv/`。旧镜像和历史虚拟环境卷保留供回滚。

切换后容器为 `healthy`、重启次数为 0，首页及 `/healthz` 正常；已验证本地文件修改实时出现在容器中。4 份配置 JSON、密码和设备身份均与备份一致。迁移到 `/opt/venv` 后，镜像在禁网条件下运行 524 项 Python 测试通过（2 项 Windows 检查跳过），并验证了离线依赖同步、`uv run` 和 ADB 路径。已实际删除宿主机 `.venv`，容器继续保持 `healthy`，原先运行的 `alas` 任务曾恢复运行；随后收到 WebUI 手动停止请求，现保留停止状态。

### 发现的问题

仅运行镜像内源码不方便本地调试；另设开发模式会增加切换负担。直接挂载仓库又会遮蔽镜像中的依赖，嵌套挂载 Python 卷还会受到宿主机删除 `.venv` 目录的影响。

### 建议修正

统一默认 Compose 为源码挂载，Python 卷挂在 `/opt/venv`，依赖同步尊重 `UV_PROJECT_ENVIRONMENT`；前端卷及 Node/npm 继续支持本地调试。

### 是否需要继续修改

本次需求已完成，配置和运行状态验证通过，无需额外切换脚本。
