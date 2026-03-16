"""
Fast JSON utilities using orjson with stdlib fallback.

orjson provides 3-10x faster JSON serialization/deserialization.
Falls back to standard json if orjson is not installed.

Usage:
    from app.utils.json_utils import json_loads, json_dumps

Performance comparison (approximate):
    - json.loads: 1x baseline
    - orjson.loads: 3-6x faster
    - json.dumps: 1x baseline
    - orjson.dumps: 5-10x faster
"""

from typing import Any, Optional, Union
import logging

logger = logging.getLogger(__name__)

# Try to import orjson, fall back to stdlib json
try:
    import orjson

    _USE_ORJSON = True
    logger.debug("Using orjson for fast JSON serialization")

    def json_loads(data: Union[str, bytes]) -> Any:
        """Parse JSON string/bytes to Python object (orjson)."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return orjson.loads(data)

    def json_dumps(
        obj: Any,
        *,
        indent: bool = False,
        sort_keys: bool = False,
        ensure_ascii: bool = True,  # Ignored by orjson (always UTF-8)
    ) -> str:
        """Serialize Python object to JSON string (orjson)."""
        options = orjson.OPT_UTC_Z
        if indent:
            options |= orjson.OPT_INDENT_2
        if sort_keys:
            options |= orjson.OPT_SORT_KEYS

        # orjson returns bytes, decode to str for compatibility
        return orjson.dumps(obj, option=options).decode("utf-8")

    def json_dumps_bytes(
        obj: Any,
        *,
        indent: bool = False,
        sort_keys: bool = False,
    ) -> bytes:
        """Serialize Python object to JSON bytes (orjson, zero-copy)."""
        options = orjson.OPT_UTC_Z
        if indent:
            options |= orjson.OPT_INDENT_2
        if sort_keys:
            options |= orjson.OPT_SORT_KEYS
        return orjson.dumps(obj, option=options)

except ImportError:
    import json

    _USE_ORJSON = False
    logger.debug("orjson not available, using stdlib json")

    def json_loads(data: Union[str, bytes]) -> Any:
        """Parse JSON string/bytes to Python object (stdlib)."""
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    def json_dumps(
        obj: Any,
        *,
        indent: bool = False,
        sort_keys: bool = False,
        ensure_ascii: bool = True,
    ) -> str:
        """Serialize Python object to JSON string (stdlib)."""
        return json.dumps(
            obj,
            indent=2 if indent else None,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )

    def json_dumps_bytes(
        obj: Any,
        *,
        indent: bool = False,
        sort_keys: bool = False,
    ) -> bytes:
        """Serialize Python object to JSON bytes (stdlib)."""
        return json_dumps(obj, indent=indent, sort_keys=sort_keys).encode("utf-8")


def is_using_orjson() -> bool:
    """Check if orjson is being used."""
    return _USE_ORJSON


# Aliases for drop-in replacement
loads = json_loads
dumps = json_dumps
