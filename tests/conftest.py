"""共享测试基建（tests/ 下全部用例自动加载）。

BC-001 Host 白名单中间件上线后，TestClient 默认 base_url（http://testserver）
发出的 Host 头不在回环白名单内，既有 201 个用例会在中间件处 403。为使这些
用例零语义改动，这里把 starlette TestClient 的默认 base_url 统一改为
http://127.0.0.1:8787（fastapi.testclient.TestClient 是同一类对象的重导出，
进程内所有 TestClient 实例自动携带合法回环 Host/Origin）。
显式传入 base_url 的构造不受影响（kwargs.setdefault 语义）。
"""
from __future__ import annotations

import starlette.testclient

_TEST_BASE_URL = "http://127.0.0.1:8787"

_original_init = starlette.testclient.TestClient.__init__


def _init_with_loopback_base_url(self, app, *args, **kwargs):
    kwargs.setdefault("base_url", _TEST_BASE_URL)
    _original_init(self, app, *args, **kwargs)


starlette.testclient.TestClient.__init__ = _init_with_loopback_base_url
