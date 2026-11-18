"""Owned, non-root CLI connection check against an explicit local daemon."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid

from docker_platform import CLI_IMAGE, cli_command, daemon_socket, remove_socket_container


def connection_smoke(*, repo, runtime, endpoint, image=CLI_IMAGE):
    repo, runtime = Path(repo).resolve(), Path(runtime).resolve()
    if runtime.is_relative_to(repo):
        raise ValueError("Socket smoke runtime must be outside the repository")
    runtime.mkdir(mode=0o700)  # Never reuse another run's home or output.
    env = {key: value for key, value in os.environ.items()
           if key not in {"DOCKER_CONTEXT", "DOCKER_DEFAULT_PLATFORM"}}
    env["DOCKER_HOST"] = endpoint
    source, gid = daemon_socket(endpoint, image=image, env=env)
    command = cli_command(["sh", "-ec", "id -u; docker version --format '{{.Server.Version}}'"],
        repo=repo, runtime=runtime, image=image, socket_path=source, socket_gid=gid)
    name = "aiv-diagnostics-socket-client-" + uuid.uuid4().hex
    command[3:3] = ["--name", name]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=True)
        uid, version = result.stdout.strip().splitlines()
        if uid != str(os.getuid()) or uid == "0" or not version:
            raise RuntimeError("Socket smoke did not confirm a non-root daemon connection")
        return {"verified": True, "daemon_socket": str(source), "daemon_socket_gid": gid,
                "client_uid": int(uid), "server_version": version}
    finally:
        remove_socket_container(["docker", "--host", endpoint], name, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True, help="new directory outside repository")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--image", default=CLI_IMAGE)
    print(json.dumps(connection_smoke(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
