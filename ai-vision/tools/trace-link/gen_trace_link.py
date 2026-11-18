import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.parse


SPACE_ID = "replace-with-space-uuid"
TRACE_ID = "replace-with-trace-id"
OBSERVATION_ID = "replace-with-observation-id"
BASIC_AUTH_B64 = "base64-of-public-key-colon-secret-key"
AIVISION_HOST = "http://127.0.0.1:5174"
TTL_SECONDS = 1800


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate signed AI Vision trace-link or observation-link URLs."
    )
    parser.add_argument(
        "--kind",
        choices=("trace", "observation"),
        default="trace",
        help="Link type to generate. Default: trace.",
    )
    parser.add_argument(
        "--observation-id",
        default=OBSERVATION_ID,
        help="Required when --kind=observation.",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Append embed=1 for iframe embedding.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.kind == "observation" and not args.observation_id:
        raise SystemExit("--observation-id is required when --kind=observation")

    decoded = base64.b64decode(BASIC_AUTH_B64).decode("utf-8")
    public_key, secret_key = decoded.split(":", 1)

    now = int(time.time())
    payload_obj = {
        "space_id": SPACE_ID,
        "trace_id": TRACE_ID,
        "iat": now,
        "exp": now + TTL_SECONDS,
        "v": 1,
    }
    if args.kind == "observation":
        payload_obj["observation_id"] = args.observation_id

    payload_json = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False)
    payload = b64url(payload_json.encode("utf-8"))
    sig = b64url(
        hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )

    path = "observation-link" if args.kind == "observation" else "trace-link"
    url = f"{AIVISION_HOST}/{path}?payload={urllib.parse.quote(payload)}&sig={urllib.parse.quote(sig)}"
    if args.embed:
        url = f"{url}&embed=1"

    print("public_key =", public_key)
    print("kind =", args.kind)
    if args.kind == "observation":
        print("observation_id =", args.observation_id)
    print("payload =", payload)
    print("sig =", sig)
    print("url =", url)


if __name__ == "__main__":
    main()
