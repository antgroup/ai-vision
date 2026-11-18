from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from agentlens.domain.enums import DatasetType
from agentlens.infra.signoz.client import SignozClient


@dataclass(frozen=True)
class SelectorInput:
    target_id: str
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class SelectorContext:
    start_time: str
    end_time: str
    task_uuid: str
    job_uuid: str
    dataset_spec: Mapping[str, Any]


@dataclass(frozen=True)
class SelectedTargetMetadata:
    exp_ver_id: str = ""
    model: str = ""


@dataclass(frozen=True)
class SelectionResult:
    selected_target_ids: list[str]
    target_metadata: dict[str, SelectedTargetMetadata]
    stats: dict[str, Any]


class SelectorAllBatchesFailed(RuntimeError):
    def __init__(self, message: str, stats: dict[str, Any]) -> None:
        super().__init__(message)
        self.stats = stats


class TargetSelector(ABC):
    selector_type: ClassVar[str]
    dataset_types: ClassVar[frozenset[DatasetType]]
    runtime_types: ClassVar[frozenset[str] | None] = None

    def __init__(self, config: dict[str, Any], client: SignozClient) -> None:
        self.config = config
        self.client = client

    @classmethod
    @abstractmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def required_fields(self) -> tuple[str, ...]: ...

    @abstractmethod
    def select(
        self,
        targets: list[SelectorInput],
        context: SelectorContext,
    ) -> SelectionResult: ...


class TargetSelectorRegistry:
    _selectors: dict[str, type[TargetSelector]] = {}

    @classmethod
    def register(cls, selector: type[TargetSelector]) -> type[TargetSelector]:
        key = selector.selector_type
        if not key or key in cls._selectors:
            raise ValueError(f"duplicate or empty target selector type: {key!r}")
        cls._selectors[key] = selector
        return selector

    @classmethod
    def normalize_config(
        cls,
        config: Any,
        dataset_type: DatasetType,
        *,
        runtime_type: str | None = None,
    ) -> dict[str, Any] | None:
        if config is None:
            return None
        if not isinstance(config, dict):
            raise ValueError("dataset spec field 'selector' must be an object")
        selector_type = config.get("type")
        if not isinstance(selector_type, str) or not selector_type.strip():
            raise ValueError("dataset spec field 'selector.type' must be a non-empty string")
        selector_type = selector_type.strip()
        selector_cls = cls._selectors.get(selector_type)
        if selector_cls is None:
            raise ValueError(f"unknown dataset selector type: {selector_type!r}")
        if dataset_type not in selector_cls.dataset_types:
            raise ValueError(
                f"dataset selector {selector_type!r} does not support {dataset_type.value!r}"
            )
        if (
            runtime_type is not None
            and selector_cls.runtime_types is not None
            and runtime_type not in selector_cls.runtime_types
        ):
            raise ValueError(
                f"dataset selector {selector_type!r} does not support runtime {runtime_type!r}"
            )
        raw_config = {key: value for key, value in config.items() if key != "type"}
        return {"type": selector_type, **selector_cls.validate_config(raw_config)}

    @classmethod
    def create(
        cls,
        config: dict[str, Any] | None,
        dataset_type: DatasetType,
        client: SignozClient,
    ) -> TargetSelector | None:
        normalized = cls.normalize_config(config, dataset_type)
        if normalized is None:
            return None
        selector_cls = cls._selectors[normalized["type"]]
        selector_config = {key: value for key, value in normalized.items() if key != "type"}
        return selector_cls(selector_config, client)
