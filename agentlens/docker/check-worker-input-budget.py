"""Qualify both compiled readers against the image-owned input capability."""

import argparse
import json
import subprocess


def qualify(image: str) -> list[dict]:
    metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    bound = 16 * 1024**2
    assert metadata["Config"]["Labels"]["io.aivision.sandbox.max-input-bytes"] == str(bound)
    manifest = json.loads(
        subprocess.check_output(
            ["docker", "run", "--rm", "--network", "none", image, "--check-runtime"]
        )
    )
    receipts = []
    for worker, entry in (
        ("restricted", "/app/src/agentlens-rule-worker"),
        ("enhanced", "/app/rule-runtime/enhanced-worker"),
        ("legacy-local", "/usr/local/bin/python"),
    ):
        for size in (bound + 1,) if worker == "legacy-local" else (3_000_000, bound, bound + 1):
            request = {
                "code_hash": "reader-budget-qualification",
                "code": 'def evaluate(trace): return {"count": len(trace["padding"])}',
                "trace": {"padding": ""},
                "timeout_seconds": 30,
            }
            if worker == "enhanced":
                request.update(
                    rule_runtime=manifest["id"], rule_runtime_manifest=manifest["manifest_sha256"]
                )
            overhead = len((json.dumps(request) + "\n").encode())
            request["trace"]["padding"] = "x" * (size - overhead)
            raw = (json.dumps(request) + "\n").encode()
            assert len(raw) == size
            process = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--memory",
                    "1g",
                    "--entrypoint",
                    entry,
                    image,
                    *(
                        [
                            "-I",
                            "-S",
                            "-c",
                            "import sys;sys.path.insert(0,'/app/src');"
                            "import agentlens_rule_worker_impl;"
                            "agentlens_rule_worker_impl.main()",
                        ]
                        if worker == "legacy-local"
                        else []
                    ),
                ],
                input=raw,
                capture_output=True,
                timeout=60,
                check=True,
            )
            result = json.loads(process.stdout)
            if size <= bound or worker == "legacy-local":
                assert result["status"] == "ok", result
                assert result["result"]["count"] == size - overhead, result
            else:
                assert result["status"] == "error" and "input exceeds bound" in result["reason"], (
                    result
                )
            receipts.append({"worker": worker, "stdin_bytes": size, "status": result["status"]})
    return receipts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    print(json.dumps(qualify(args.image), indent=2))
