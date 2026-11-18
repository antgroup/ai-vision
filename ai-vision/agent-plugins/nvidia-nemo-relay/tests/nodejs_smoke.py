"""Run the actual Node SDK example against a local OTLP collector.

Needs node >=24, a --node-project containing nemo-relay-node, yaml, typescript,
and @types/node, plus opentelemetry-proto in this Python environment. No model,
platform service, production credentials, or external telemetry endpoint is used.
"""

import argparse
import base64
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def attributes(items):
    return {item.key: getattr(item.value, item.value.WhichOneof("value")) for item in items}


def printed_trace_ids(output):
    return [line.split(":", 1)[1].strip() for line in output.splitlines()
            if line.startswith("AI Vision trace_id:")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-project", type=Path, required=True)
    parser.add_argument("--node", default="node", help="Node.js >=24 executable")
    args = parser.parse_args()
    project = args.node_project.resolve()
    source = Path(__file__).resolve().parents[1] / "examples/nodejs.yaml"
    template = json.loads(subprocess.check_output([
        args.node, "-e",
        "const fs=require('node:fs'); console.log(JSON.stringify(require('yaml').parse(fs.readFileSync(process.argv[1],'utf8'))))",
        str(source),
    ], cwd=project, text=True))
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            request = ExportTraceServiceRequest.FromString(body)
            received.append((self.path, dict(self.headers), request))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    authorization = "Basic " + base64.b64encode(b"smoke-agent:smoke-space").decode()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    environment.update({
        "AIVISION_OTLP_ENDPOINT": f"http://127.0.0.1:{collector.server_port}/api/public/otel/v1/traces",
        "AIVISION_OTLP_AUTHORIZATION": authorization,
        "AIVISION_SERVICE_NAME": "relay-nodejs-smoke",
    })
    try:
        with tempfile.TemporaryDirectory(prefix=".relay-smoke-", dir=project) as temporary:
            folder = Path(temporary)
            example = folder / "relay-nodejs.ts"
            example.write_text(template["npmExample"])
            (folder / "package.json").write_text('{"type":"module"}')
            existing = folder / "existing.ts"
            existing.write_text(template["existingExample"] + '''
async function existingAgent(input: JsonValue) { return { echo: input }; }
try {
  const first = await handleRequest("existing-first", "first response");
  const second = await handleRequest("existing-second", { value: false });
  console.log("EXISTING_RESULT:", JSON.stringify([first, second]));
} finally { await onShutdown(); }
''')
            subprocess.run([
                args.node, str(project / "node_modules/typescript/bin/tsc"), "--noEmit", "--module", "nodenext",
                "--target", "es2022", "--strict", "--skipLibCheck", "--allowImportingTsExtensions",
                str(example), str(existing),
            ], cwd=project, check=True)
            complete = subprocess.run([args.node, str(example)], cwd=project, env=environment,
                                      capture_output=True, text=True, timeout=30)
            assert complete.returncode == 0, complete.stderr
            demo_trace_ids = printed_trace_ids(complete.stdout)
            assert len(demo_trace_ids) == 1, complete.stdout
            demo_trace = demo_trace_ids[0]
            assert len(demo_trace) == 32, complete.stdout
            assert "63" in complete.stdout.splitlines(), complete.stdout
            scenario = folder / "scenario.ts"
            scenario.write_text('''
import { startTracing, stopTracing, runWithTrace } from "./relay-nodejs.ts";
import relay from "nemo-relay-node";
startTracing(); startTracing();
try {
  const results = await Promise.all([
    runWithTrace("session-a", { value: 0 }, async (metadata) => {
      const tool = await relay.toolCallExecuteAsync("tool-a", { value: 0 }, async () => {
        await new Promise(resolve => setTimeout(resolve, 20));
        return { result: 0 };
      }, null, null, null, metadata);
      return tool.result as number;
    }, "user-a"),
    runWithTrace("session-b", { value: false }, async (metadata) => {
      const tool = await relay.toolCallExecuteAsync("tool-b", { value: false }, async () => ({ result: false }), null, null, null, metadata);
      return tool.result as boolean;
    }, "user-b"),
  ]);
  const anonymous = await runWithTrace("session-anonymous", null, async () => null);
  console.log("ERROR_REQUEST_START");
  try {
    await runWithTrace("session-error", "fail", async () => { throw new Error("expected failure"); });
    throw new Error("request error was swallowed");
  } catch (error) {
    if (!(error instanceof Error) || error.message !== "expected failure") throw error;
  }
  console.log("ERROR_REQUEST_END");
  console.log("SCENARIO_RESULT:", JSON.stringify({ results, anonymous }));
} finally { await stopTracing(); await stopTracing(); }
''')
            result = subprocess.run([args.node, str(scenario)], cwd=project, env=environment,
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            outcomes = json.loads(result.stdout.split("SCENARIO_RESULT:", 1)[1])
            assert [entry["result"] for entry in outcomes["results"]] == [0, False]
            error_output = result.stdout.split("ERROR_REQUEST_START", 1)[1].split("ERROR_REQUEST_END", 1)[0]
            error_trace_ids = printed_trace_ids(error_output)
            assert len(error_trace_ids) == 1, "Failed requests must print their trace ID: " + result.stdout
            error_trace_id = error_trace_ids[0]
            assert len(printed_trace_ids(result.stdout)) == 4, result.stdout
            existing_result = subprocess.run([args.node, str(existing)], cwd=project, env=environment,
                                            capture_output=True, text=True, timeout=30)
            assert existing_result.returncode == 0, existing_result.stderr
            returned = json.loads(existing_result.stdout.split("EXISTING_RESULT:", 1)[1])
            assert returned == [{"echo": "first response"}, {"echo": {"value": False}}]
            existing_trace_ids = printed_trace_ids(existing_result.stdout)
            assert len(existing_trace_ids) == 2
            before = len(received)
            missing_auth = {**environment, "AIVISION_OTLP_AUTHORIZATION": ""}
            failure = subprocess.run([args.node, str(example)], cwd=project, env=missing_auth,
                                     capture_output=True, text=True, timeout=30)
            assert failure.returncode != 0 and "authorization" in failure.stderr
            assert len(received) == before

        spans = []
        for path, headers, request in received:
            assert path == "/api/public/otel/v1/traces", path
            normalized = {key.lower(): value for key, value in headers.items()}
            assert normalized["authorization"] == authorization
            assert normalized["content-type"] == "application/x-protobuf"
            for resource in request.resource_spans:
                resource_attrs = attributes(resource.resource.attributes)
                assert resource_attrs["service.name"] == "relay-nodejs-smoke"
                assert "langfuse.user.id" not in resource_attrs
                assert "authorization" not in resource_attrs
                for scope in resource.scope_spans:
                    spans.extend(scope.spans)
        assert len(spans) == 10, [(span.name, attributes(span.attributes)) for span in spans]
        by_trace = {}
        for span in spans:
            by_trace.setdefault(span.trace_id.hex(), []).append(span)
        assert len(by_trace) == 7
        for trace_id, group in by_trace.items():
            roots = [span for span in group if not span.parent_span_id]
            assert len(roots) == 1, (trace_id, [span.name for span in group])
            root = roots[0]
            root_attrs = attributes(root.attributes)
            assert root_attrs["aivision.request.input.encoding"] == "json"
            assert root_attrs["aivision.request.output.encoding"] == "json"
            for child in [span for span in group if span.parent_span_id]:
                child_attrs = attributes(child.attributes)
                assert child.parent_span_id == root.span_id
                assert child_attrs["gen_ai.conversation.id"] == root_attrs["gen_ai.conversation.id"]
                assert child_attrs.get("langfuse.user.id") == root_attrs.get("langfuse.user.id")
                assert "aivision.request.input" not in child_attrs
            session = root_attrs["gen_ai.conversation.id"]
            if session in ("session-a", "session-b"):
                suffix = session[-1]
                assert root_attrs["langfuse.user.id"] == f"user-{suffix}"
                expected = 0 if suffix == "a" else False
                actual = json.loads(root_attrs["aivision.request.output"])
                assert actual == expected and type(actual) is type(expected)
            else:
                assert "langfuse.user.id" not in root_attrs
            if session == "session-error":
                assert trace_id == error_trace_id, "Printed failure trace ID must identify the exported ERROR root"
                assert root.status.code == 2, root
                assert json.loads(root_attrs["aivision.request.output"]) is None
            if trace_id == demo_trace:
                assert len(group) == 2
                assert json.loads(root_attrs["aivision.request.output"]) == 63
        for outcome in outcomes["results"] + [outcomes["anonymous"]]:
            assert outcome["traceId"] in by_trace
        assert all(trace_id in by_trace for trace_id in existing_trace_ids)
        assert error_trace_id in by_trace
        version = json.loads((project / "node_modules/nemo-relay-node/package.json").read_text())["version"]
        runtime = subprocess.check_output([args.node, "--version"], text=True).strip()
        print(f"PASS: Node.js {runtime}, nemo-relay-node {version}, TypeScript, {len(spans)} spans, 7 traces; "
              "auth/path/protobuf, root IO, parent links, request isolation, error trace ID, missing-auth failure, "
              "existing service lifecycle and business results")
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join()


if __name__ == "__main__":
    main()
