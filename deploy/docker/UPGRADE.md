# Docker 部署与升级

Compose 使用镜像内的 Python 3.14.6、`uv.lock` 锁定依赖和预构建 React 前端。Node 24 仅用于构建前端，运行容器不需要 npm 安装依赖。

持久化目录：`config/`、`log/`、`cache/`、`files/`、`AzurPilot_Data_Backup/`。`log/device_id.json` 随日志目录保留，确保本地统计数据库的设备身份连续。源码和 `.venv` 不再通过宿主机目录或旧命名卷覆盖。

## 更新

在仓库根目录执行：

```bash
docker compose build
docker compose up -d --no-build ALAS
docker compose ps
```

默认镜像为 `alas:py314`。可通过 `ALAS_IMAGE` 指定经验证的版本标签，通过 `GIT_REVISION` 写入镜像的源码版本标签。端口由 `ALAS_WEBUI_PORT` 设置，默认 `22267`；启动参数与 `/healthz` 健康检查共用该值。健康检查验证实际 API 返回值。

每次升级前，应保存旧镜像标签并在停止容器后备份 `config/`。不要删除旧虚拟环境卷或运行 `docker compose down -v`，旧版本回滚可能仍需使用该卷。

## 2026-09-19 迁移记录

- 合并代码：`b84d53afe`，包含上游 dev `6e8b93c7b`，保留 [clean 策略](../../CLEAN_POLICY.md)。
- 新镜像：`alas:clean-dev-20260919`。
- 旧镜像：`alas:py314-rollback-20260919-0379d948a`。
- 回滚源码、原 Compose、回滚 Compose 和切换前配置备份：`/home/dreamydust/alas_py314/backups/20260919-clean-dev-docker/`。
- 原虚拟环境卷：`azurlaneautoscript_alas-venv`，保留用于回滚。
- 原服务继续使用 host 网络与 `22267` 端口。

### 切换验证

新镜像 ID 为 `sha256:4f2d23c273c93ef20557b96adf57495cbd4f3c20eb8dff52199013f0157a8932`，已同时标记为 `alas:py314` 并用于正式容器。运行环境为 Python 3.14.6、uv 0.11.32。

镜像在 `--network none`、`TZ=Asia/Shanghai` 下运行 521 项 Python 测试成功，其中 2 项 Windows 检查跳过。独立隔离容器的首页与健康接口检查通过，预构建前端无需联网即可启动。

正式服务切换后 `/healthz` 返回正常，容器 `healthy` 且重启次数为 0；4 份配置 JSON 与切换前逐字节一致，原密码及 `log/device_id.json` 保持一致。本地时钟状态确认未启用 NTP。配置和日志压缩备份均已完成并验证可读取，SQLite `quick_check` 通过。

宿主机 `/home/dreamydust/alas_py314/restart.sh` 已改为按固定名称 `alas` 重启，避免容器重建后旧 ID 失效。

回滚操作会停止当前服务。先妥善保存升级后的数据，再按需要恢复切换前配置，最后使用备份目录中的 Compose：

```bash
docker compose -p azurlaneautoscript \
  -f /home/dreamydust/alas_py314/backups/20260919-clean-dev-docker/rollback.compose.yml \
  up -d --no-build ALAS
```

该回滚描述使用独立保存的旧源码与原虚拟环境，不依赖当前分支的工作区内容。
