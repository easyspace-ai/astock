"""全局测试隔离: 默认数据目录不落到运行者本机的真实数据目录。

本地 `.env` 常把 `DATA_DIR` 指向生产数据 (含真实 preferences / users.db),
依赖默认配置的测试会读到个人配置而失败 (例如实时行情源被改成 tdxrs,
或已有注册用户导致未登录请求返回 401)。这里在每个测试前把
`settings.data_dir` 指向独立的临时目录, 让测试结果只取决于代码本身。

需要真实目录的测试可以自行 monkeypatch `settings.data_dir` 覆盖本夹具。
"""
from __future__ import annotations

import pytest


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
