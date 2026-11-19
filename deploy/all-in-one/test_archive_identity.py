"""Verify that a saved image still matches the identity recorded in its receipt."""

import argparse
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("all_in_one_build", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


TAG = "aiv-all-in-one:test"
PLATFORM = build.PLATFORMS["arm64"]


def config(marker):
    return json.dumps({
        "os": "linux",
        "architecture": "arm64",
        "rootfs": {"diff_ids": list(PLATFORM["base_layers"])},
        "marker": marker,
    }).encode()


def docker_save(config_bytes):
    data = io.BytesIO()
    manifest = [{"Config": "config.json", "RepoTags": [TAG], "Layers": []}]
    with tarfile.open(fileobj=data, mode="w") as saved:
        for name, content in (("manifest.json", json.dumps(manifest).encode()), ("config.json", config_bytes)):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            saved.addfile(member, io.BytesIO(content))
    data.seek(0)
    return data


class BuildArchiveIdentityTest(unittest.TestCase):
    def test_build_rejects_archive_after_tag_changes(self):
        inspected_config = config("inspected")
        image = {
            "Id": "sha256:" + hashlib.sha256(inspected_config).hexdigest(),
            "Os": "linux", "Architecture": "arm64", "Size": 1024,
            "RootFS": {"Layers": list(PLATFORM["base_layers"])},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "context"
            context.mkdir()
            (context / build.RECEIPT).write_text("{}", encoding="utf-8")
            output = root / "image.tar.gz"
            receipt = output.with_name(output.name + ".json")
            args = argparse.Namespace(context=context, archive=output, tag=f"docker.io/library/{TAG}", base=None)
            with (
                patch.object(build, "verify_context", return_value=("arm64", {})),
                patch.object(build, "inspect_image", return_value=image),
                patch.object(build.subprocess, "run"),
                patch.object(build.subprocess, "Popen") as save,
            ):
                child = save.return_value.__enter__.return_value
                child.wait.return_value = 0
                child.stdout = docker_save(config("replacement"))
                with self.assertRaisesRegex(ValueError, "does not match the inspected image ID"):
                    build.build(args)
                self.assertFalse(output.exists())
                self.assertFalse(receipt.exists())
                self.assertEqual(set(root.iterdir()), {context})

                child.stdout = docker_save(inspected_config)
                build.build(args)
                saved = json.loads(receipt.read_text())
                self.assertEqual(saved["image_id"], image["Id"])
                self.assertEqual(saved["image_tag"], TAG)
                self.assertEqual(saved["archive_sha256"], build.sha256(output))
                save.assert_called_with(["docker", "image", "save", args.tag], stdout=build.subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
