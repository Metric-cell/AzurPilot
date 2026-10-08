"""本地异常日志不记录堆栈局部变量。"""
import unittest

from module.logger import console_hdlr


class ChannelTests(unittest.TestCase):
    def test_exception_locals_are_not_logged(self):
        self.assertFalse(console_hdlr.tracebacks_show_locals)
