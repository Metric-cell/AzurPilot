from deploy.config_transaction import DeployConfigTransaction
import sys
from typing import Optional, Union

from deploy.logger import logger
from deploy.utils import *


MANUAL_GIT_MIRRORS = [
    'https://cnb.cool/AzurPilot/AzurPilot',
    'https://gitcode.com/ddl2/AzurPilot',
]


class ErrorCodes:
    """部署错误码：同时作为进程退出码返回，启动器按码分类展示。

    码段与 gui.py 的 EXIT_*（70-76）互不重叠；新增码值必须同步
    启动器（alas-launcher）的错误码映射表。
    """

    EXEC_UNKNOWN = 1
    COMMAND_FAILED = 10      # execute() 通用命令执行失败
    GIT_FETCH_FAILED = 11    # git fetch 主源与全部镜像重试穷尽
    GIT_MERGE_FAILED = 12    # git pull/merge 合并失败
    DEPS_SYNC_FAILED = 13    # 依赖同步（uv sync / pip）失败


class ExecutionError(Exception):
    """部署流程执行失败；code 取 ErrorCodes，供上游结构化识别。"""

    def __init__(self, code: int = ErrorCodes.EXEC_UNKNOWN, message: str = ''):
        super().__init__(message or f'deploy failed with error code {code}')
        self.code = code


class ConfigModel:
    """部署配置模型，定义所有配置项及其默认值。"""
    # Git 配置
    Repository: str = "https://github.com/wess09/AzurPilot"
    Branch: str = "master"
    GitExecutable: str = "./.venv/Scripts/git/cmd/git.exe" if sys.platform == "win32" else "./.venv/bin/git"
    GitProxy: Optional[str] = None
    SSLVerify: bool = False

    # Python 配置
    PythonExecutable: str = "./.venv/Scripts/python.exe" if sys.platform == "win32" else "./.venv/bin/python"
    PypiMirror: Optional[str] = None
    InstallDependencies: bool = True

    # ADB 配置
    AdbExecutable: str = "./.venv/Scripts/adb.exe" if sys.platform == "win32" else "./.venv/bin/adb"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    # OCR 配置
    UseOcrServer: bool = False
    StartOcrServer: bool = False
    OcrServerPort: int = 22268
    OcrClientAddress: str = "127.0.0.1:22268"

    # 更新配置
    EnableReload: bool = True

    # WebUI 配置
    WebuiHost: str = "0.0.0.0"
    WebuiPort: int = 25548
    WebuiSSLKey: Optional[str] = None
    WebuiSSLCert: Optional[str] = None
    Language: str = "en-US"
    Theme: str = "default"
    DpiScaling: bool = True
    Password: Optional[str] = None
    CDN: Union[str, bool] = False
    # --watermark. 关闭未经验证版本的水印。默认 False，即默认显示水印，
    # 用于提醒当前运行的是未经验证的构建（见 module/webui/app_shell.py）。
    DisableBranchWatermark: bool = False
    Run: Optional[str] = None


class DeployConfig(DeployConfigTransaction, ConfigModel):
    """部署配置管理器，负责配置的读取、保存、重定向与执行环境管理。"""

    def __init__(self, file=DEPLOY_CONFIG):
        """初始化部署配置。

        Args:
            file (str): 用户部署配置文件路径。
        """
        self.file = file
        self.template_file = get_deploy_template()
        self.config = {}
        self.config_template = {}
        self.read()

        self.show_config()

    def show_config(self):
        """打印与默认模板不同的配置项。"""
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            if k in ("Password",):
                continue
            if self.config_template.get(k) == v:
                continue
            logger.info(f"{k}: {v}")

        logger.info(f"Rest of the configs are the same as default")

    def config_redirect(self):
        """部署配置重定向，处理旧配置到新配置的迁移。

        每次 `read()` 之后必须调用。
        """
        if self.PypiMirror in [
            'https://pypi.tuna.tsinghua.edu.cn/simple'
        ]:
            object.__setattr__(self, 'PypiMirror', 'https://mirrors.aliyun.com/pypi/simple')
            self.config['PypiMirror'] = 'https://mirrors.aliyun.com/pypi/simple'

        if self.Repository in ['global']:
            super().__setattr__('Repository', 'https://github.com/wess09/AzurPilot')
        if self.Repository in ['cn']:
            super().__setattr__('Repository', 'https://github.com/wess09/AzurPilot')
        if self.Repository == 'cnb':
            super().__setattr__('Repository', MANUAL_GIT_MIRRORS[0])
        if self.Repository == 'gitcode':
            super().__setattr__('Repository', MANUAL_GIT_MIRRORS[1])

    def filepath(self, key):
        """根据配置键获取绝对文件路径。

        Args:
            key (str): 配置键名。

        Returns:
            str: 绝对文件路径。
        """
        return (
            os.path.abspath(os.path.join(self.root_filepath, self.config[key]))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    @cached_property
    def root_filepath(self):
        """获取项目根目录绝对路径。

        Returns:
            str: 格式化为斜杠分隔的根目录绝对路径。
        """
        return (
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    def execute(self, command, allow_failure=False, output=True):
        """执行系统命令。

        Args:
            command (str): 要执行的命令。
            allow_failure (bool): 是否允许失败。
            output (bool): 是否显示输出。

        Returns:
            bool: 是否成功。失败且不允许失败时终止安装流程。
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        if not output:
            command = command + ' >nul 2>nul'
        logger.info(command)
        error_code = os.system(command)
        if error_code:
            if allow_failure:
                logger.info(f"[ allowed failure ], error_code: {error_code}")
                return False
            else:
                logger.info(f"[ failure ], error_code: {error_code}")
                self.show_error(command)
                raise ExecutionError(
                    ErrorCodes.COMMAND_FAILED,
                    f'Command failed (exit {error_code}): {command}',
                )
        else:
            logger.info(f"[ success ]")
            return True

    def show_error(self, command=None):
        """展示更新失败信息及排查指引。

        Args:
            command (str, optional): 导致失败的命令。
        """
        logger.hr("Update failed", 0)
        self.show_config()
        logger.info("")
        logger.info(f"Last command: {command}")
        logger.info(
            "Please check your deploy settings in config/deploy.yaml "
            "and re-open AzurPilot.exe"
        )
        logger.info("Take the screenshot of entire window if you need help")
