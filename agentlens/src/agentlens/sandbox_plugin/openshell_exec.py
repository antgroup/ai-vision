"""Pinned OpenShell 0.1.6 stdin transport; imported only inside the optional provider.

The Python SDK streams output but sends all stdin in one unary request. Use its
generated bidirectional RPC for larger input, as the upstream CLI does. Keep the
1 MiB gateway decoder bound and the independent total worker input bound intact.
"""

from __future__ import annotations

import re

from agentlens.domain.sandbox import SandboxError

GRPC_MESSAGE_BYTES = 1024 * 1024
STDIN_FRAME_BYTES = 4096


def prepare(name, command, stdin, *, workspace, timeout_seconds):
    from openshell._proto import datamodel_pb2
    from openshell._proto import openshell_pb2 as pb

    request = pb.ExecSandboxRequest(
        workspace_scope=datamodel_pb2.WorkspaceSelector(workspace=workspace),
        sandbox=name,
        command=list(command),
        stdin=stdin,
        no_login_shell=True,
        tty=False,
    )
    request.execution_timeout.seconds = timeout_seconds
    size = request.ByteSize()
    if size <= GRPC_MESSAGE_BYTES:
        return request, {"transport": "unary", "rpc_bytes": size}
    request.stdin = b""
    start_size = pb.ExecSandboxInput(start=request).ByteSize()
    if start_size > GRPC_MESSAGE_BYTES:
        raise SandboxError(
            "sandbox_input_limit",
            details={
                "stage": "grpc_message",
                "actual_bytes": start_size,
                "allowed_bytes": GRPC_MESSAGE_BYTES,
            },
        )
    return request, {"transport": "streaming", "rpc_bytes": start_size}


def stream(client, name, command, *, workspace, stdin, timeout_seconds, no_login_shell):
    import grpc
    from openshell._proto import openshell_pb2 as pb
    from openshell.sandbox import ExecChunk, ExecResult

    if not no_login_shell:
        raise ValueError("The rule worker cannot use a login shell")
    request, info = prepare(
        name, command, stdin, workspace=workspace, timeout_seconds=timeout_seconds
    )

    def frames():
        yield pb.ExecSandboxInput(start=request)
        for offset in range(0, len(stdin), STDIN_FRAME_BYTES):
            yield pb.ExecSandboxInput(stdin=stdin[offset : offset + STDIN_FRAME_BYTES])
        # Clean request-stream EOF closes remote stdin; keep draining the response.

    deadline = max(client._timeout, timeout_seconds + 10)
    call = (
        client._stub.ExecSandbox(request, timeout=deadline)
        if info["transport"] == "unary"
        else client._stub.ExecSandboxInteractive(frames(), timeout=deadline)
    )
    exit_code = None
    try:
        for event in call:
            kind = event.WhichOneof("payload")
            if exit_code is not None:
                raise ValueError("Provider sent data after exit")
            if kind in {"stdout", "stderr"}:
                yield ExecChunk(stream=kind, data=bytes(getattr(event, kind).data))
            elif kind == "exit":
                exit_code = int(event.exit.exit_code)
            else:
                raise ValueError("Provider returned an invalid execution event")
        if exit_code is None:
            raise ValueError("Provider execution did not complete")
        # Reaching stream EOF also checks the final gRPC status, after the exit event.
        yield ExecResult(exit_code=exit_code, stdout="", stderr="")
    except grpc.RpcError as error:
        detail = error.details() or ""
        match = re.fullmatch(
            r"Error, decoded message length too large: found (\d+) bytes, "
            r"the limit is: (\d+) bytes",
            detail[:256],
        )
        if error.code() == grpc.StatusCode.OUT_OF_RANGE and match:
            raise SandboxError(
                "sandbox_input_limit",
                details={
                    "stage": "grpc_message",
                    "actual_bytes": int(match[1]),
                    "allowed_bytes": int(match[2]),
                },
            ) from None
        raise
    finally:
        # Generator close/output overflow must cancel the actual RPC and its sender.
        call.cancel()
