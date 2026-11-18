#!/usr/bin/env python3
"""Expose schemas from pinned dependency CRDs to Kubeconform (requires PyYAML).

This checks structural schema compatibility only. Admission webhooks, CEL
validation rules and real cluster lifecycle remain separate acceptance checks.
"""

import argparse
import json
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("manifests", type=Path, nargs="+")
    args = parser.parse_args()
    count = 0
    for path in args.manifests:
        for document in yaml.safe_load_all(path.read_text()):
            if not document or document.get("kind") != "CustomResourceDefinition":
                continue
            spec = document["spec"]
            for version in spec["versions"]:
                schema = version.get("schema", {}).get("openAPIV3Schema")
                if schema is None:
                    raise ValueError(f"{path}: CRD {spec['names']['kind']} has no schema for {version['name']}")
                destination = args.out / spec["group"] / (
                    spec["names"]["kind"].lower() + "_" + version["name"] + ".json")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(schema, sort_keys=True) + "\n")
                count += 1
    if count == 0:
        raise ValueError("No dependency CRD schemas were found")
    print(f"Extracted {count} schemas from pinned dependency manifests.")


if __name__ == "__main__":
    main()
