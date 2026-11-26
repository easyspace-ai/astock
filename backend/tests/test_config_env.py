from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def test_settings_reads_server_values_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in ("HOST", "PORT", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HOST=127.0.0.1\n"
        "PORT=4318\n"
        "LOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )

    configured = Settings(_env_file=env_path)

    assert configured.host == "127.0.0.1"
    assert configured.port == 4318
    assert configured.log_level == "DEBUG"
    assert not hasattr(configured, "auth_password")
