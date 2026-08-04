"""JSON tree type aliases shared by package root and emit modules.

Kept free of package-root imports so ``canonical`` / ``identity`` can annotate
public helpers without circular imports or TYPE_CHECKING-only names that break
``typing.get_type_hints``.
"""

from __future__ import annotations

from typing import TypeAlias

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

__all__ = ["JsonPrimitive", "JsonValue", "JsonObject"]
