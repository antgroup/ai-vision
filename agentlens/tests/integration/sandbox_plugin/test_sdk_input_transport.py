"""Real SDK/protobuf/gRPC transport with a local 1 MiB decoder, not an OpenShell runtime."""

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agentlens.sandbox_plugin.provider import OpenShellProvider

sdk = pytest.importorskip("openshell.sandbox")


@pytest.fixture
def transport():
    import grpc
    from openshell._proto import openshell_pb2 as pb
    from openshell._proto import openshell_pb2_grpc as rpc

    received = []
    cancelled = threading.Event()

    def respond(command, data, context):
        if command[-1] == "overflow":
            context.add_callback(cancelled.set)
            yield pb.ExecSandboxEvent(stdout=pb.ExecSandboxStdout(data=b"x" * 65536))
            cancelled.wait(5)
            return
        yield pb.ExecSandboxEvent(
            stdout=pb.ExecSandboxStdout(data=hashlib.sha256(data).hexdigest().encode())
        )
        if command[-1] == "missing-exit":
            return
        yield pb.ExecSandboxEvent(exit=pb.ExecSandboxExit(exit_code=0))
        if command[-1] == "late-error":
            context.abort(grpc.StatusCode.INTERNAL, "synthetic final status failure")

    class Server(rpc.OpenShellServicer):
        def ExecSandbox(self, request, context):
            received.append(("unary", bytes(request.stdin)))
            yield from respond(request.command, request.stdin, context)

        def ExecSandboxInteractive(self, requests, context):
            first = next(requests)
            assert first.WhichOneof("payload") == "start"
            assert first.start.stdin == b"" and not first.start.tty
            assert first.start.no_login_shell
            data = bytearray()
            for frame in requests:
                assert frame.WhichOneof("payload") == "stdin"
                assert 0 < len(frame.stdin) <= 4096
                data.extend(frame.stdin)
            received.append(("streaming", bytes(data)))
            yield from respond(first.start.command, data, context)

    server = grpc.server(
        ThreadPoolExecutor(max_workers=2),
        options=[
            ("grpc.max_receive_message_length", 1048576),
        ],
    )
    rpc.add_OpenShellServicer_to_server(Server(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    client = object.__new__(sdk.SandboxClient)
    client._stub, client._timeout = rpc.OpenShellStub(channel), 5
    provider = OpenShellProvider(
        SimpleNamespace(load_policy=lambda: {}, workspace="default", operation_timeout_seconds=5)
    )
    provider._client = client
    try:
        yield provider, received, cancelled
    finally:
        channel.close()
        server.stop(0).wait(5)


@pytest.mark.parametrize(
    "size,mode",
    [
        (900000, "unary"),
        (1048576, "streaming"),
        (1600000, "streaming"),
        (2097152, "streaming"),
    ],
)
def test_complete_stdin_crosses_decoder_limit_without_loss(transport, size, mode):
    provider, received, _ = transport
    data = ("中文🙂".encode() * (size // 10 + 1))[:size]
    result = provider.execute("owned", ["/worker"], data, 3, 100)
    assert result.exit_code == 0
    assert result.stdout == hashlib.sha256(data).hexdigest()
    assert received == [(mode, data)]


def test_request_metadata_cannot_hide_behind_streaming(transport):
    from agentlens.domain.sandbox import SandboxError

    provider, received, _ = transport
    with pytest.raises(SandboxError) as raised:
        provider.execute("owned", ["x" * 1048576], b"", 3, 100)
    assert raised.value.code == "sandbox_input_limit"
    assert raised.value.details["stage"] == "grpc_message"
    assert received == []


@pytest.mark.parametrize("protobuf_size", [1048575, 1048576, 1048577])
def test_exact_serialized_request_boundary(transport, protobuf_size):
    from openshell._proto import datamodel_pb2
    from openshell._proto import openshell_pb2 as pb

    provider, received, _ = transport
    request = pb.ExecSandboxRequest(
        workspace_scope=datamodel_pb2.WorkspaceSelector(workspace="default"),
        sandbox="owned",
        command=["/worker"],
        stdin=b"x" * 1048000,
        no_login_shell=True,
    )
    request.execution_timeout.seconds = 3
    data = b"x" * (len(request.stdin) + protobuf_size - request.ByteSize())
    request.stdin = data
    assert request.ByteSize() == protobuf_size
    output = provider.execute("owned", ["/worker"], data, 3, 100)
    assert output.stdout == hashlib.sha256(data).hexdigest()
    assert received == [("unary" if protobuf_size <= 1048576 else "streaming", data)]


@pytest.mark.parametrize("size", [10, 1600000])
@pytest.mark.parametrize("mode", ["missing-exit", "late-error"])
def test_success_requires_exit_and_final_rpc_status(transport, size, mode):
    import grpc

    provider, _, _ = transport
    with pytest.raises(ValueError if mode == "missing-exit" else grpc.RpcError):
        provider.execute("owned", ["/worker", mode], b"x" * size, 3, 100)


@pytest.mark.parametrize("size", [10, 1600000])
def test_output_limit_cancels_real_rpc(transport, size):
    provider, _, cancelled = transport
    output = provider.execute("owned", ["/worker", "overflow"], b"x" * size, 3, 32)
    assert output.truncated
    assert output.stdout == "x" * 32
    assert cancelled.wait(2), "The output limit must cancel the actual provider RPC"
