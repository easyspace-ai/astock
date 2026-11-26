"""全局测试隔离: 默认数据目录不落到运行者本机的真实数据目录。

本地 `.env` 常把 `DATA_DIR` 指向生产数据 (含真实 preferences / users.db),
依赖默认配置的测试会读到个人配置而失败 (例如实时行情源被改成 tdxrs,
或已有注册用户导致未登录请求返回 401)。这里在每个测试前把
`settings.data_dir` 指向独立的临时目录, 让测试结果只取决于代码本身。

需要真实目录的测试可以自行 monkeypatch `settings.data_dir` 覆盖本夹具。
"""
from __future__ import annotations

import os

# 必须在任何 app.main 导入之前生效: app.main 在导入时会挂载生产 data/logs
# 文件 handler, 仅靠下面的 settings.data_dir monkeypatch 已经晚了一步。
os.environ["TSP_DISABLE_FILE_LOG"] = "1"

import pytest  # noqa: E402 — 必须在 TSP_DISABLE_FILE_LOG 之后导入 app


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path_factory, monkeypatch):
    from app.config import settings

    # 避免子进程 (如持锁进程) 继承运行者本机的数据目录。
    monkeypatch.delenv("DATA_DIR", raising=False)
    # 放在测试临时根目录下的独立子目录, 而不是 tmp_path 内:
    # 有些测试会把 tmp_path 当成行情库根目录并断言其目录内容,
    # 多出的子目录会破坏这类断言。
    app_data = tmp_path_factory.mktemp("app_data")
    monkeypatch.setattr(settings, "data_dir", app_data)
    return app_data


@pytest.fixture(autouse=True)
def legacy_fixed_date_live_writes(monkeypatch, request):
    """兼容用固定历史日期模拟"当天"的既有实时写盘测试。

    生产守卫 (finalization.live_write_block_reason) 要求实时写盘只能发生在
    北京今天且早于 15:35; 大量历史测试为了确定性使用 2020/2026-07 等固定
    date 调用 flush/merge, 会被守卫正确拒绝。守卫本身由
    tests/test_finalization.py 和 tests/test_data_integrity.py 覆盖, 这里只
    对这两个文件以外的测试放开日期限制, 避免把测试夹具的"假今天"当成生产
    历史分区。
    """
    nodeid = request.node.nodeid
    if "test_finalization.py" in nodeid or "test_data_integrity.py" in nodeid:
        return
    from app.services import finalization
    monkeypatch.setattr(finalization, "live_write_block_reason", lambda *a, **k: None)
