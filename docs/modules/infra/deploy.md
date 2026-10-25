# 部署与启动链路（deploy/ + 启动入口）

> 从「拿到一份源码」到「WebUI 起来、实例跑起来」的全部工程环节：安装器、启动脚本、Docker、依赖自举与前端构建。

桌面启动器的预构建组件模式另见本文末尾的「桌面启动器组件模式」。该模式不执行下文的本机 Git/npm/uv 自举流程；开发、源码和容器启动仍沿用原路径。

## 1. 模块概述

`deploy/` 不是游戏业务，而是把仓库变成可运行系统的安装与启动工程。它要解决三件事：

- **自举**：用户机器上可能只有系统 Python。安装器先用它引导 `uv`，再由 `uv` 建出仓库内 `.venv/`，之后一切 Python、git、adb 都从 `.venv/` 取——系统环境零污染，依赖完全由 `pyproject.toml` + `uv.lock` 锁定。
- **安装**：显式调用常规 git 拉仓库（指定国内镜像时支持同级容灾）、同步依赖、装 uiautomator2、连模拟器、构建前端，全部封装成 `python -m deploy.installer` 一个命令。
- **依赖同步**：安装器是显式部署入口，WebUI 启动时复用 `deploy/uv.py` 同步依赖。clean 分支移除后台源码更新、云端控制、IP 定位与 Git over CDN。

## 2. 模块职责

### 负责

- `installer.py`：安装总入口，组合五个管理器（git → 停调度器 → 依赖 → 前端 → adb）。
- `uv.py`：`.venv` 自举、uv 解析、依赖同步（`sync_project_venv`）、常驻依赖同步服务（`dependency_sync_service`，gui.py 运行期调用）。
- `git.py`：显式仓库部署（常规 git 与用户指定镜像的同级容灾）、随机 User-Agent 绕镜像封禁。
- `adb.py` / `emulator.py`：adb 分发与自动连接；VirtualBox 系模拟器注册表扫描与端口发现。
- `frontend.py`：按源码内容摘要决定是否重建 `frontend/dist`。
- `atomic.py`：安装失败的原子清理（临时文件识别与回滚）。
- `patch.py`：安装前环境修补（运行目录检查、uiautomator2/apkutils2 补丁、证书链）。
- `launcher/Alas.bat`（打包为 `AzurPilot.exe`）：用户双击入口——跑安装器，成功后用 `pythonw` 拉起 `gui.py`。

### 不负责

- WebUI 的运行期进程管理——见[运行时服务](../webui/runtime.md)。
- 游戏识别资源与 OCR 模型——安装器只保证 adb/uiautomator2，模型随仓库分发。
- 配置内容（`config/deploy.yaml` 是部署参数，语义见第 10 节）。

## 3. 模块位置

```
deploy/
├── installer.py     # 总入口：Installer(GitManager, PipManager, AdbManager, AppManager, AlasManager)
├── config.py        # DeployConfig（config/deploy.yaml 的模型）+ ExecutionError
├── git.py             # 显式仓库部署；无 CDN 客户端
├── pip.py / uv.py   # 依赖同步（实际是 uv sync，类名保留 pip 是历史）
├── adb.py / emulator.py     # adb 安装与模拟器探测（Windows 注册表 + VirtualBox）
├── frontend.py      # ensure_frontend：源码摘要 → npm ci + build
├── app.py           # AppManager：安装期前端构建步骤
├── patch.py         # pre_checks：路径检查、site-packages 补丁
├── atomic.py        # 失败清理（Windows 文件占用重试）
├── launcher/Alas.bat        # 双击入口（AzurPilot.exe 的本体）
├── Windows/         # Windows 专用 deploy 变体（模板 yaml 等）
├── docker/          # Dockerfile（含 cn 镜像源变体）与构建脚本
├── install/         # 平台辅助（模拟器探测）
└── AidLux/          # 安卓本地部署方案
```

## 4. 核心入口

| 入口 | 用途 |
| --- | --- |
| `python -m deploy.installer` | 全新安装/更新（Alas.bat 即调用它） |
| `AzurPilot.exe` / `deploy/launcher/Alas.bat` | 用户入口：安装器成功后 `pythonw gui.py` |
| `uv run python gui.py` | 开发态启动 WebUI（跳过安装器） |
| `python -m deploy.app` | 只重建前端产物 |
| `deploy/docker/Dockerfile` | 容器构建（多阶段，预装前端产物） |

### 本地历史持久化

clean 分支删除交易所远端服务和后台上传，但图形调度与行动力历史仍使用本地身份和加密存储。新版统一持久化在 `config/stock-exchange/`，包括 `game.key`、`registry.json`、身份与历史检查点。首次升级需同时保留旧 `cache/stock-exchange/` 可读写，程序迁移到 config 后仍使用原身份；不能因目录名称包含 stock 就删除本地数据。

备份和迁移前停止服务与 worker，再一起复制配置、SQLite（含 WAL）、旧 CL1 统计库、设备身份及本地加密数据。旧格式升级须在原容器项目路径下进行，保留原用户密钥目录中的 `.game` 登记与配套 `.key`。账号保险库的项目外本机密钥与 machine-id 继续按原部署方式保留，它们与行动力历史的文件密钥是不同的保护机制。

统计库与舰船经验的新写入使用普通 SQLite/JSON；`opsi_secure` 仅负责旧统计密文的本地兼容读取和迁移，不恢复日报或上传服务。保留旧日报数据库的迁移与备份能力，避免丢失已有文件。迁移成功才清理对应旧统计密钥；升级前完整备份，不删除密钥、登记或身份文件来重建已有数据。完整 clean 边界见 [CLEAN_POLICY.md](../../../CLEAN_POLICY.md)。

## 6. 工作流程

### 安装器链条

```
Alas.bat → python -m deploy.installer
  patch.pre_checks()          # 运行目录、site-packages 补丁
  git_install()               # 常规 git；仅显式国内镜像支持同级容灾
  alas_kill()                 # 停掉本实例的 alas 进程
  pip_install()               # 实为 uv sync（--frozen），失败抛 ExecutionError
  app_update()                # ensure_frontend()：摘要变化才 npm ci + build
  adb_install()               # adb 就位、替换系统 adb、自动连接、装 uiautomator2
失败 → atomic_failure_cleanup → 退出码 1 → bat pause
成功 → start pythonw gui.py --electron
```

`GitManager.git_install()` 按本地配置直接执行 Git/CDN 更新，不再请求云端更新总开关。云端仅保留运行期间的强制更新开关，其访问失败不阻止正常版本检查。

### 启动后的依赖自举

仓库的 `[tool.uv] cache-dir` 与启动器的 `UV_CACHE_DIR` 统一指向项目内 `.uv-cache/`，
直接执行 `uv run gui.py` 和启动器内部同步均不依赖 Windows 用户全局缓存目录的写权限。
该目录由 uv 管理并已忽略；不需要修改系统缓存 ACL 或重建 `.venv`。用户显式设置的
`UV_CACHE_DIR` 或 `--cache-dir` 仍可覆盖项目默认值。

`gui.py` 不直接信任 `.venv`：启动时用 `deploy/uv.py` 的 `dependency_sync_service` 做带超时与重试的依赖同步（pending 状态记录在 `module/runtime/setting.py`，崩溃后下次启动重做）。WebUI 服务有 120 秒就绪超时与最多 3 次重启；运行期检测到依赖标记时同样先同步再拉服务。

### 前端构建判据

`ensure_frontend` 对 `package.json`、`vite.config.ts`、`src/**`、`public/**` 做内容哈希（不依赖 git 检出时间），摘要变化才重建。这就是「改前端源码后不重启构建，界面不更新」的原因。

## 10. 配置

部署参数在 `config/deploy.yaml`（模型见 `deploy/config.py`）：git 仓库/分支/代理/SSL、Python 与 uv、镜像源、adb 替换与自动连接、OCR 服务器与自动重启时刻。安装器读它，WebUI 的部署设置页写它——两边经同一份文件交互。

## 11. 异常与错误处理

| 情况 | 处理 |
| --- | --- |
| 任一管理器抛 `ExecutionError` | 安装器 `exit(1)`，bat `pause` 停留展示日志 |
| uv sync 失败 | 输出 uv 日志（敏感文本经 `redact_sensitive_text` 脱敏）后中止 |
| git 418/镜像封禁 | 随机 git User-Agent；用户指定国内镜像时尝试同级镜像 |
| Windows 文件占用 | `atomic.py` 按重试上限等待后清理 |
| `.venv` 损坏 | `uv.py` 检测 python 不可用 → 自举重建 |

## 16. 修改注意事项

- **`PipManager` 名字是历史遗留**：它调用 `uv sync`，不要在其中加 pip 逻辑；依赖问题先查 `deploy/uv.py`。
- **`deploy/Windows/` 是旧布局副本**，主流程已用顶层 `deploy/*.py`；改安装逻辑改顶层，勿两边都改。
- **前端产物摘要机制**：只改 `dist/` 不改源码会被下次启动重建覆盖；发布产物请走正常构建。
- **手动镜像容灾**：`MANUAL_GIT_MIRRORS` 在主部署模块与 Windows 副本中保持一致，当前包含 CNB 与 GitCode 的 AzurPilot 仓库。仅显式部署请求使用；`cn` 别名沿用 GitHub，不恢复自动分流、定位或云端更新。
- `Alas.bat` 转 `AzurPilot.exe` 由外部工具完成，仓库只维护 bat。

## 17. 已知限制

- 模拟器自动探测仅支持 Windows 注册表可见的 VirtualBox 系模拟器；其他场景手动填 serial。
- 安装器假设能访问 git 远端或用户指定镜像；完全离线的首次安装不可行。

## 20. 相关模块

- [WebUI 启动器](../entry/gui.md) —— gui.py 的监督与依赖同步运行期行为
- [WebUI 总览](../webui/index.md) —— 更新事务与进程监督全貌
- [运行时服务](../webui/runtime.md) —— updater 与 installer 共享的依赖同步
- [外部桥接与开发工具](submodule-tools.md) —— dev_tools 中的生成器（CI 侧的对应物）

## 桌面启动器组件模式

桌面启动器从唯一的启动器 `stable.json` 下载 CI 编译的源码、游戏资源、前端、Python 3.14 最新稳定补丁、锁定依赖与 ADB，然后设置 `AZURPILOT_PREBUILT=1`、`AZURPILOT_NO_UV_BOOTSTRAP=1` 启动 `gui.py`。Python 的可用版本范围为 `>=3.14.6,<3.15`。

`module/runtime/prebuilt.py` 读取 CI 的 `BUILD_MANIFEST` 与 `.azurpilot/installed.json`，检查源码提交、解释器版本及必需组件，并拒绝尚有部署事务日志的环境。GUI 监督器在此模式下校验组件并清除旧依赖同步标志，不启动 uv 同步服务。前端指纹失配时明确要求通过启动器重新部署，不执行 npm 构建。

WebUI 的更新状态和历史从构建清单读取，不调用 Git；界面显示由桌面启动器管理，停止服务并再次启动可取得新组件。Git 更新与依赖同步接口在预构建模式下拒绝执行，普通源码启动不受影响。`config/deploy.yaml` 仍用于后端业务选项，不承担桌面启动器的安装职责。

后端 `master` 更新后，可由 `.github/workflows/launcher-components.yml` 使用 `LAUNCHER_DISPATCH_TOKEN` 通知 `wess09/AzurPilotLauncher` 重新编译组件；未配置该跨仓库令牌时，启动器仓库的定时检查仍会更新组件。CI 的内部清单片段不作为独立线上 JSON 发布，运行元数据统一合并到原启动器 `stable.json`。
