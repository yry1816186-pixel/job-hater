"""ARCH-002（随 BC-007 落地）: WeChatService 生命周期契约。

- 实例在 lifespan 启动时创建并挂 app.state.wechat（不再是路由注册期的进程单例）；
- 应用关闭（lifespan 结束）时调用 stop_scan 停掉后台扫描线程；
- 路由处理器经 request.app.state.wechat 访问服务（同一 app 内可替换，非闭包捕获）；
- _stop_flag 为 threading.Event：start_scan 清除、stop_scan 置位并等待线程退出，
  后台线程经 is_set() 观察（替代裸 bool 的跨线程读写）。
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api.app import create_app


def test_wechat_service_lives_on_app_state(tmp_path):
    config.set_data_dir(tmp_path)
    app = create_app()
    assert not hasattr(app.state, "wechat"), "路由注册期不得实例化 WeChatService（ARCH-002）"
    with TestClient(app) as c:
        from jobhater.services.wechat.service import WeChatService

        assert isinstance(app.state.wechat, WeChatService)
        assert c.get("/api/wechat/status").status_code == 200
    config.set_data_dir(None)


def test_lifespan_shutdown_stops_scan(tmp_path):
    config.set_data_dir(tmp_path)
    app = create_app()
    with TestClient(app):
        wx = app.state.wechat
        stopped: list[bool] = []
        _orig = wx.stop_scan

        def _spy() -> None:
            stopped.append(True)
            _orig()

        wx.stop_scan = _spy
    assert stopped == [True], "lifespan 关闭时必须调用 stop_scan 收尾后台线程"
    config.set_data_dir(None)


def test_wechat_routes_consult_app_state(tmp_path):
    """处理器必须读 request.app.state.wechat——替换实例后行为随实例变化。"""
    config.set_data_dir(tmp_path)
    app = create_app()
    with TestClient(app) as c:
        stub = SimpleNamespace(
            status=lambda: {"phase": "stubbed", "running": False},
            stop_scan=lambda: None,
        )
        real = app.state.wechat
        app.state.wechat = stub
        try:
            r = c.get("/api/wechat/status")
            assert r.status_code == 200
            assert r.json()["phase"] == "stubbed"
            assert c.post("/api/wechat/stop").json()["phase"] == "stubbed"
        finally:
            app.state.wechat = real
    config.set_data_dir(None)


def test_two_apps_have_independent_wechat_services(tmp_path):
    config.set_data_dir(tmp_path)
    a1, a2 = create_app(), create_app()
    with TestClient(a1), TestClient(a2):
        assert a1.state.wechat is not a2.state.wechat
    config.set_data_dir(None)


# ---------- _stop_flag: threading.Event 语义 ----------


def test_stop_flag_is_threading_event():
    from jobhater.services.wechat.service import WeChatService

    svc = WeChatService()
    assert isinstance(svc._stop_flag, threading.Event)
    assert not svc._stop_flag.is_set()
    svc.stop_scan()  # 无后台线程在跑：仅置位，不应抛错
    assert svc._stop_flag.is_set()


def test_start_scan_clears_event_and_stop_joins_thread(tmp_path, monkeypatch):
    config.set_data_dir(tmp_path)
    from jobhater.services.wechat.service import WeChatService

    svc = WeChatService()

    def _blocked_scan(min_confidence: float) -> None:
        deadline = time.monotonic() + 5
        while not svc._stop_flag.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        svc.state.phase = "idle"

    monkeypatch.setattr(svc, "_scan_impl", _blocked_scan)
    svc._stop_flag.set()  # 预置停止位：start_scan 必须先清除才能真跑
    out = svc.start_scan(min_confidence=0.35)
    assert out["running"] is True, "start_scan 应清除遗留停止位后再启动线程"
    svc.stop_scan()
    assert svc._stop_flag.is_set()
    assert svc.status()["running"] is False, "stop_scan 应等待后台线程退出"
    config.set_data_dir(None)
