"""Build packaging must use compiler provenance and never archive owned Python."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch
import build_native_onboarding as builder
import tempfile
import tarfile
import unittest

from build_native_onboarding import dependency_destinations, package_archive, verify_elf_target


class NativeOnboardingBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.upstream = self.root / 'python/lib'
        self.upstream.mkdir(parents=True)
        self.report = self.root / 'report.xml'
        self.allowed = {str(self.upstream): 'CPython fixture upstream root'}
        self.variables = {'sys.real_prefix': str(self.root / 'python')}

    def write_report(self, extra='', own_kind='CompiledPythonModule'):
        self.report.write_text(f'''<nuitka-compilation-report nuitka_version="4.3.1" mode="standalone" completion="yes">
          <module name="__main__" kind="PythonMainModule" source_path="{self.source}/connect"/>
          <module name="native_config" kind="{own_kind}" source_path="{self.source}/native_config.py"/>
          <module name="hook_runtime" kind="CompiledPythonModule" source_path="{self.source}/hook_runtime.py"/>
          <module name="json" kind="UncompiledPythonModule" source_path="${{sys.real_prefix}}/lib/python3.11/json/__init__.py"/>
          <included_extension dest_path="_socket.so" source_path="${{sys.real_prefix}}/lib/python3.11/lib-dynload/_socket.so" ignored="no"/>
          {extra}</nuitka-compilation-report>''')

    def destinations(self):
        return dependency_destinations(self.report, self.source, self.allowed, self.variables)

    def test_compiler_report_maps_upstream_payload_with_provenance(self):
        self.write_report()
        result = self.destinations()
        self.assertEqual(set(result), {'_socket.so'})
        self.assertIn('CPython fixture upstream root', result['_socket.so'])
        self.assertIn(str(self.upstream), result['_socket.so'])

    def test_owned_module_cannot_remain_uncompiled(self):
        self.write_report(own_kind='UncompiledPythonModule')
        with self.assertRaisesRegex(ValueError, 'compiled'):
            self.destinations()

    def test_owned_python_cannot_be_copied_as_data_or_dependency(self):
        self.write_report(f'<data_file dest_path="native_config.py" source_path="{self.source}/native_config.py"/>')
        with self.assertRaisesRegex(ValueError, 'owned'):
            self.destinations()

    def test_unmapped_or_unsafe_compiler_payload_fails_closed(self):
        for extra in [
            '<included_dll dest_path="libunknown.so" source_path="/unreviewed/lib.so" ignored="no"/>',
            '<included_dll dest_path="../escape.so" source_path="${sys.real_prefix}/lib/ok.so" ignored="no"/>',
        ]:
            with self.subTest(extra=extra):
                self.write_report(extra)
                with self.assertRaises(ValueError):
                    self.destinations()

    def test_archive_layout_and_metadata_are_deterministic(self):
        bundle = self.root / 'distribution'
        bundle.mkdir()
        binary = bundle / 'connect'
        binary.write_bytes(b'\x7fELFnative')
        binary.chmod(0o755)
        (bundle / 'libpython.so').write_bytes(b'\x7fELFupstream')
        first, second = self.root / 'first.tar.gz', self.root / 'second.tar.gz'
        package_archive(bundle, first)
        import os
        os.utime(binary, (5000, 5000))
        package_archive(bundle, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(archive.getnames(), ['connect', 'connect/connect', 'connect/libpython.so'])
            self.assertTrue(all(member.isfile() or member.isdir() for member in archive.getmembers()))
            self.assertEqual(archive.getmember('connect/connect').mode, 0o755)
            self.assertEqual(archive.getmember('connect/libpython.so').uid, 0)

    def arguments(self, **overrides):
        values = dict(compiler_python=sys.executable, target='linux-gnu-arm64', source_root=self.source,
                      work_dir=self.root / 'work', output_dir=self.root / 'output',
                      dependency_root=[str(self.upstream) + '=fixture upstream'])
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_compiler_mutating_any_owned_input_rejects_artifact_before_publication(self):
        tools = self.root / 'tools'
        tools.mkdir()
        for name in ['connect', 'native_config.py', 'hook_runtime.py']:
            (self.source / name).write_text('initial source')
        for name in ['build_native_onboarding.py', 'native_artifacts.py']:
            (tools / name).write_text('initial build tool')
        runtime = json.dumps(dict(python=[3, 11, 14], nuitka='4.3.1', system='Linux', machine='aarch64',
                                  base_prefix=str(self.root / 'python'), prefix=str(self.root / 'compiler')))
        for changed in [self.source / 'native_config.py', tools / 'build_native_onboarding.py', tools / 'native_artifacts.py']:
            with self.subTest(changed=changed):
                original = changed.read_text()
                arguments = self.arguments(output_dir=self.root / ('output-' + changed.name))
                def compiler_edits_input(command, **keywords):
                    if '-m' not in command:
                        return
                    changed.write_text(original + '\nconcurrent edit')
                    self.write_report()
                    target = arguments.output_dir / arguments.target
                    (target / 'nuitka-report.xml').write_text(self.report.read_text())
                    distribution = arguments.work_dir / 'connect.dist'
                    distribution.mkdir(parents=True, exist_ok=True)
                    header = bytearray(64)
                    header[:6] = b'\x7fELF\x02\x01'
                    header[18:20] = (183).to_bytes(2, 'little')
                    for filename in ('connect', '_socket.so'):
                        (distribution / filename).write_bytes(header)
                    (distribution / 'connect').chmod(0o755)
                try:
                    with patch.object(builder, '__file__', str(tools / 'build_native_onboarding.py')), \
                         patch.object(builder.subprocess, 'check_output', return_value=runtime), \
                         patch.object(builder.subprocess, 'run', side_effect=compiler_edits_input):
                        with self.assertRaisesRegex(ValueError, 'inputs changed'):
                            builder.build(arguments)
                    self.assertFalse((arguments.output_dir / arguments.target / 'connect.tar.gz').exists())
                    self.assertFalse((arguments.output_dir / arguments.target / 'build-info.json').exists())
                finally:
                    changed.write_text(original)

    def test_build_output_and_distribution_overlaps_are_rejected_before_writes(self):
        cases = [dict(work_dir=self.root), dict(output_dir=self.root),
                 dict(work_dir=self.source / 'work'), dict(output_dir=self.source / 'output'),
                 dict(output_dir=self.root / 'work/connect.dist')]
        runtime = json.dumps(dict(python=[3, 11, 14], nuitka='4.3.1', system='Linux', machine='aarch64'))
        for overrides in cases:
            with self.subTest(overrides=overrides), patch.object(builder.subprocess, 'check_output', return_value=runtime), patch.object(builder.subprocess, 'run', side_effect=AssertionError('compiler invoked before overlap guard')):
                with self.assertRaisesRegex(ValueError, 'overlap'):
                    builder.build(self.arguments(**overrides))
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertFalse((self.root / 'work').exists())

    def test_elf_architecture_must_match_requested_target(self):
        binary = self.root / 'connect'
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        header[18:20] = (183).to_bytes(2, 'little')
        binary.write_bytes(header)
        verify_elf_target(binary, 'linux-gnu-arm64')
        with self.assertRaisesRegex(ValueError, 'architecture'):
            verify_elf_target(binary, 'linux-gnu-x64')


if __name__ == '__main__':
    unittest.main()
