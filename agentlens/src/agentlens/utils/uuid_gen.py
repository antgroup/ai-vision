import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits  # [a-z0-9]


def _rand8() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def new_diagnoser_uuid() -> str:
    return f"dg-{_rand8()}"


def new_dataset_uuid() -> str:
    return f"ds-{_rand8()}"


def new_job_uuid() -> str:
    return f"j-{_rand8()}"


def new_task_uuid() -> str:
    return f"t-{_rand8()}"


def new_model_connection_uuid() -> str:
    return f"m-{_rand8()}"
