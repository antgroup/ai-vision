class DomainError(Exception):
    """所有 agentlens 域异常的基类。"""


class NotFound(DomainError): ...


class Conflict(DomainError): ...  # 状态冲突 / 唯一键冲突


class Forbidden(DomainError): ...  # 鉴权失败之外的"操作不允许"


class Validation(DomainError): ...


class InvalidTimeRange(DomainError): ...


class ExternalUnavailable(DomainError): ...  # SigNoz / 下游 HTTP 失败
