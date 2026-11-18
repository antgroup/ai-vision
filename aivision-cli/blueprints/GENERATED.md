# Generated deployment assets

The YAML files in this directory are generated compatibility copies of the authoritative
`deploy/` sources. Do not edit the copies directly.

From the repository root, run:

```sh
python3 deploy/scripts/package_assets.py
python3 deploy/scripts/package_assets.py --check
```

The complete source mapping and checksums are recorded in
`aivision-cli/deploy/assets-manifest.json`.
