import random
import time

from deploy.config import DeployConfig, ExecutionError
from deploy.logger import logger
from deploy.utils import *


class GitManager(DeployConfig):
    @cached_property
    def git(self):
        exe = self.filepath('GitExecutable')
        if os.path.exists(exe):
            return exe

        logger.warning(f'GitExecutable: {exe} does not exist, use `git` instead')
        return 'git'

    @staticmethod
    def remove(file):
        try:
            os.remove(file)
            logger.info(f'Removed file: {file}')
        except FileNotFoundError:
            logger.info(f'File not found: {file}')

    @staticmethod
    def git_user_agent():
        """生成随机的 git User-Agent，绕开部分镜像仓库对特定 git 版本的封禁。

        版本号各段与构建后缀均由随机数生成器产出，每次更新 UA 都不同，
        避免命中 gitcode 等仓库针对特定 UA 字符串的封禁（418）。
        """
        while True:
            major = random.randint(2, 3)
            minor = random.randint(30, 59)
            patch = random.randint(0, 9)
            build = random.randint(1, 5)
            sub = random.randint(1, 9999)
            if random.random() < 0.3:
                ua = f'git/{major}.{minor}.{patch}'
            elif random.random() < 0.6:
                ua = f'git/{major}.{minor}.{patch}.windows.{build}'
            else:
                ua = f'git/{major}.{minor}.{patch}.windows.{build}.{sub}'
            if not ua.startswith('git/2.51.0.windows.2'):
                break
        return ua

    def _fetch_with_retry(self, source, branch, max_retry=5, delay=2):
        """带 UA 重试的 git fetch。

        gitcode 等仓库会返回 418 拦截特定 UA，此时自动更换 UA 重试。
        不需要遍历 init/config/remote——那些不涉及 HTTP 请求。

        Args:
            source: 远程源名称。
            branch: 分支名称。
            max_retry: 最大尝试次数（含首次）。
            delay: 重试间隔秒数。

        Raises:
            ExecutionError: 所有尝试均失败时抛出。
        """
        ua = self.git_user_agent()
        for i in range(max_retry):
            git = f'"{self.git}" -c http.userAgent={ua}'
            logger.info(f'Use git User-Agent: {ua}')
            if self.execute(f'{git} fetch {source} {branch}'):
                return
            logger.warning(f'git fetch failed with UA {ua}, attempt {i + 1}/{max_retry}')
            if i < max_retry - 1:
                time.sleep(delay)
                ua = self.git_user_agent()
        raise ExecutionError

    def git_repository_init(
            self, repo, source='origin', branch='master', proxy='', ssl_verify=True
    ):
        # 所有 git 命令统一带随机 UA，绕过 gitcode 等仓库对特定 UA 的 418 封禁
        git = f'"{self.git}" -c http.userAgent={self.git_user_agent()}'

        logger.hr('Git Init', 1)
        if not self.execute(f'{git} init', allow_failure=True):
            self.remove('./.git/config')
            self.remove('./.git/index')
            self.remove('./.git/HEAD')
            self.execute(f'{git} init')

        logger.hr('Set Git Proxy', 1)
        if proxy:
            self.execute(f'{git} config --local http.proxy {proxy}')
            self.execute(f'{git} config --local https.proxy {proxy}')
        else:
            self.execute(f'{git} config --local --unset http.proxy', allow_failure=True)
            self.execute(f'{git} config --local --unset https.proxy', allow_failure=True)

        if ssl_verify:
            self.execute(f'{git} config --local http.sslVerify true', allow_failure=True)
        else:
            self.execute(f'{git} config --local http.sslVerify false', allow_failure=True)

        logger.hr('Set Git Repository', 1)
        if not self.execute(f'{git} remote set-url {source} {repo}', allow_failure=True):
            self.execute(f'{git} remote add {source} {repo}')

        logger.hr('Fetch Repository Branch', 1)
        self._fetch_with_retry(source, branch)

        logger.hr('Pull Repository Branch', 1)
        # 移除 git 锁文件
        for lock_file in [
            './.git/index.lock',
            './.git/HEAD.lock',
            './.git/refs/heads/master.lock',
        ]:
            if os.path.exists(lock_file):
                logger.info(f'Lock file {lock_file} exists, removing')
                os.remove(lock_file)
        self.execute(f'{git} reset --hard {source}/{branch}')
        self.execute(f'{git} pull --ff-only {source} {branch}')

        logger.hr('Show Version', 1)
        self.execute(f'{git} --no-pager log --no-merges -1')

    def git_install(self):
        logger.hr('Update AzurPilot', 0)
        self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
            ssl_verify=self.SSLVerify,
        )


if __name__ == '__main__':
    GitManager().git_install()
