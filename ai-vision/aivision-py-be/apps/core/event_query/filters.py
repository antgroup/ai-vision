from dataclasses import dataclass
from enum import StrEnum


class FilterOperator(StrEnum):
    EQ = "="
    IN = "IN"
    NOT_IN = "NOT IN"
    GTE = ">="
    LTE = "<="
    LIKE = "LIKE"
    MATCH_TEXT = "MATCH_TEXT"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"
    RAW = "__RAW__"


@dataclass(frozen=True)
class FilterSpec:
    field: str
    operator: FilterOperator
    value: object | None = None
    raw_field: bool = False


@dataclass(frozen=True)
class OrderSpec:
    field: str
    ascending: bool = True
