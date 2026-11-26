"""日志脱敏 — 在日志 handler 的最终文本上替换凭证类字段。

为什么在 Formatter 层做: 业务代码可能把 dict/headers/URL 直接交给 logger,
record.args 与 traceback 里的凭证不会在 Filter 层被完整覆盖。Formatter 是
最后一次统一改写机会, 对 root/uvicorn 的所有 handler 都生效。
"""
from __future__ import annotations

import logging
import re

_REDACTED = "***"

# key=value / key: value / "key": "value" / Authorization: Bearer xxx
_SECRET_PAIR = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|"
    r"authorization|cookie|webhook[_-]?url)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^,;\s}\]]+)"
)

# URL query 中的凭证; 只替换值, 保留键名便于排障。
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password|key)=)([^&\s]+)"
)

# Authorization: Bearer/Basic xxx (整段替换, 避免只换掉 Bearer 留下 token)
_AUTH_HEADER = re.compile(
    r"(?i)(authorization\s*[=:]\s*)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
# 孤立的 Bearer / Basic 凭证
_AUTH_VALUE = re.compile(r"(?i)(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")

# 飞书/企业微信机器人 Webhook 自身就是凭证, 正文/异常里出现时必须遮住尾部。
_FEISHU_HOOK = re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+")
_WECOM_HOOK = re.compile(
    r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]+"
)


def redact_text(text: str) -> str:
    """替换文本中的常见凭证, 保持其余上下文不变。"""
    if not text:
        return text
    text = _FEISHU_HOOK.sub("https://open.feishu.cn/open-apis/bot/v2/hook/" + _REDACTED, text)
    text = _WECOM_HOOK.sub(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + _REDACTED, text,
    )
    text = _QUERY_SECRET.sub(lambda m: m.group(1) + _REDACTED, text)
    # Bearer/Basic 必须在 key:value 之前处理, 否则 Authorization 的值先被换成
    # ***, 后面的 token 会裸露 ("Authorization: *** sk-xxx")。
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _AUTH_VALUE.sub(lambda m: m.group(1) + " " + _REDACTED, text)
    text = _SECRET_PAIR.sub(lambda m: m.group(1) + m.group(2) + _REDACTED, text)
    return text


class RedactingFormatter(logging.Formatter):
    """包住原 Formatter, 输出前统一脱敏。"""

    def __init__(self, inner: logging.Formatter) -> None:
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(self._inner.format(record))

    def formatException(self, ei) -> str:  # noqa: N802 — stdlib 同名钩子
        return redact_text(self._inner.formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802 — stdlib 同名钩子
        return redact_text(self._inner.formatStack(stack_info))


def install_redaction() -> None:
    """给 root 与 uvicorn 的现有 handler 套上脱敏 Formatter。

    幂等: 已是 RedactingFormatter 的 handler 跳过。新增 handler 后应再次调用,
    因此 main.py 在文件 handler 挂载完成后统一安装。
    """
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            formatter = handler.formatter
            if formatter is not None and not isinstance(formatter, RedactingFormatter):
                handler.setFormatter(RedactingFormatter(formatter))
