"""Precision candidates must be reproducible and remain local to this run."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class SigNozPrecisionTests(unittest.TestCase):
    def precision(self):
        self.assertIsNotNone(importlib.util.find_spec('signoz_precision'),
                             'Fresh CI needs the versioned SigNoz precision candidate builder')
        import signoz_precision
        return signoz_precision

    def test_archive_checksum_rejects_modified_source_before_extraction(self):
        precision = self.precision()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.tar.gz'
            path.write_bytes(b'changed upstream source')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                precision.verify_archive(path, hashlib.sha256(b'approved source').hexdigest())

    def test_archive_rejects_traversal_and_wrong_commit_root(self):
        precision = self.precision()
        for member in ('../outside', 'wrong-commit/go.mod'):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root/'source.tar.gz'
                with tarfile.open(archive, 'w:gz') as packed:
                    item = tarfile.TarInfo(member)
                    item.size = 1
                    packed.addfile(item, io.BytesIO(b'x'))
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    precision.extract_source(archive, root/'source')
                self.assertFalse((root.parent/'outside').exists())

    def test_build_settings_require_exact_go_and_keep_tests_native(self):
        precision = self.precision()
        self.assertEqual(precision.validate_go_version('go version go1.25.7 linux/amd64\n'), '1.25.7')
        for wrong in ('go version go1.25.6 linux/amd64', 'go version go1.26.0 linux/amd64', ''):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                precision.validate_go_version(wrong)
        source = {'PATH': '/tools', 'GOOS': 'windows', 'GOARCH': 'arm64', 'CGO_ENABLED': '1'}
        native = precision.build_environment(source, cross_compile=False)
        self.assertNotIn('GOOS', native)
        self.assertNotIn('GOARCH', native)
        candidate = precision.build_environment(source, cross_compile=True)
        self.assertEqual((candidate['GOOS'], candidate['GOARCH'], candidate['CGO_ENABLED']),
                         ('linux', 'amd64', '0'))

    def test_candidate_records_binary_provenance_after_tests_and_never_publishes(self):
        precision = self.precision()
        self.assertTrue(hasattr(precision, 'build_candidate'), 'Fresh deployment needs a tested local image')
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            fake = CandidateTools()
            def download(path):
                write_archive(path, precision.SOURCE_COMMIT)
                precision.SOURCE_ARCHIVE_SHA256 = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(precision, 'download_source', side_effect=download), \
                    patch.object(precision, 'SOURCE_ARCHIVE_SHA256', precision.SOURCE_ARCHIVE_SHA256):
                result = precision.build_candidate(fake, root, runtime, 'aiv-e2e-test',
                                                    'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
            self.assertEqual(result['binary_sha256'], hashlib.sha256(b'tested executable').hexdigest())
            self.assertEqual(result['source_head'], '1deab460854209ca0b18094d10128dcd0598aea9')
            self.assertEqual(result['image_id'], 'sha256:' + 'b'*64)
            self.assertFalse(result['candidate_published'])
            self.assertFalse(result['official_fixed_image_published'])
            self.assertEqual(fake.native_test_platform, ('', ''))
            self.assertEqual(fake.build_platform, ('linux', 'amd64', '0'))
            self.assertFalse(fake.published)

    def test_failed_precision_tests_cannot_create_an_image(self):
        precision = self.precision()
        self.assertTrue(hasattr(precision, 'build_candidate'), 'Precision tests must gate candidate creation')
        with tempfile.TemporaryDirectory() as temporary:
            fake = CandidateTools(fail_tests=True)
            def download(path):
                write_archive(path, precision.SOURCE_COMMIT)
                precision.SOURCE_ARCHIVE_SHA256 = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(precision, 'download_source', side_effect=download), \
                    patch.object(precision, 'SOURCE_ARCHIVE_SHA256', precision.SOURCE_ARCHIVE_SHA256):
                with self.assertRaisesRegex(RuntimeError, 'precision regression'):
                    precision.build_candidate(fake, Path(__file__).resolve().parent, Path(temporary),
                                              'aiv-e2e-test', 'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
            self.assertFalse(fake.image_built)
            self.assertFalse(list(Path(temporary).rglob('signoz')))

    def test_go_cache_writes_cannot_reach_inherited_runner_caches(self):
        precision = self.precision()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root/'runtime'
            runtime.mkdir()
            external = root/'runner-caches'
            fake = BuildResourceTools(external)
            with candidate_source(precision):
                precision.build_candidate(fake, Path(__file__).resolve().parent, runtime,
                                          'aiv-e2e-test', 'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
            self.assertEqual(sorted(p.relative_to(external).as_posix() for p in external.rglob('*')
                                    if p.is_file()), ['build/keep', 'modules/keep'])
            self.assertTrue(fake.private_cache_inputs_existed_at_image_build)

    def test_success_reclaims_build_inputs_after_recording_provenance(self):
        precision = self.precision()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            with candidate_source(precision):
                result = precision.build_candidate(CandidateTools(), Path(__file__).resolve().parent,
                                                    runtime, 'aiv-e2e-test',
                                                    'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
            self.assertEqual(result['binary_sha256'], hashlib.sha256(b'tested executable').hexdigest())
            self.assertEqual(result['image_id'], 'sha256:' + 'b'*64)
            self.assertEqual(list(runtime.iterdir()), [])

    def test_failed_test_or_image_build_reclaims_owned_inputs(self):
        precision = self.precision()
        for failed_stage in ('tests', 'image'):
            with self.subTest(stage=failed_stage), tempfile.TemporaryDirectory() as temporary:
                runtime = Path(temporary)/'runtime'
                runtime.mkdir()
                sibling = Path(temporary)/'unrelated'
                sibling.write_text('retain this file')
                fake = CandidateTools(fail_tests=failed_stage == 'tests', fail_image=failed_stage == 'image')
                with candidate_source(precision), self.assertRaises(RuntimeError):
                    precision.build_candidate(fake, Path(__file__).resolve().parent, runtime,
                                              'aiv-e2e-test', 'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
                self.assertEqual(list(runtime.iterdir()), [])
                self.assertEqual(sibling.read_text(), 'retain this file')

    def test_image_build_receives_an_executable_without_buildkit_copy_flags(self):
        precision = self.precision()
        fake = CandidateTools()
        with tempfile.TemporaryDirectory() as temporary, candidate_source(precision):
            precision.build_candidate(fake, Path(__file__).resolve().parent, Path(temporary),
                                      'aiv-e2e-test', 'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
        if os.name != 'nt':
            self.assertEqual(fake.binary_mode_at_image_build, 0o755)
        self.assertEqual(fake.dockerfile_at_image_build.splitlines()[1], 'COPY signoz /app/signoz')

    def test_cleanup_refuses_a_foreign_candidate_image(self):
        precision = self.precision()
        self.assertTrue(hasattr(precision, 'remove_candidate'), 'Candidate cleanup needs ownership verification')
        fake = CandidateTools(owner='aiv-e2e-other')
        with self.assertRaisesRegex(ValueError, 'another run'):
            precision.remove_candidate(fake, 'aiv-e2e-test', 'aivision-signoz-e2e:aiv-e2e-test')
        self.assertFalse(fake.image_removed)
        own = CandidateTools()
        precision.remove_candidate(own, 'aiv-e2e-test', 'aivision-signoz-e2e:aiv-e2e-test')
        self.assertTrue(own.image_removed)

    def test_cleanup_proves_absence_when_candidate_was_never_created(self):
        precision = self.precision()
        fake = CandidateTools(image_present=False)
        precision.remove_candidate(fake, 'aiv-e2e-test', 'aivision-signoz-e2e:aiv-e2e-test')
        self.assertFalse(fake.image_removed)

    def test_cleanup_detects_an_image_that_remains_after_remove(self):
        precision = self.precision()
        fake = CandidateTools(remove_fails=True)
        with self.assertRaisesRegex(ValueError, 'still exists'):
            precision.remove_candidate(fake, 'aiv-e2e-test', 'aivision-signoz-e2e:aiv-e2e-test')


def write_archive(path, commit):
    with tarfile.open(path, 'w:gz') as archive:
        item = tarfile.TarInfo('signoz-' + commit + '/go.mod')
        content = b'module fixture\ngo 1.25.7\n'
        item.size = len(content)
        archive.addfile(item, io.BytesIO(content))


def candidate_source(precision):
    def download(path):
        write_archive(path, precision.SOURCE_COMMIT)
        precision.SOURCE_ARCHIVE_SHA256 = hashlib.sha256(path.read_bytes()).hexdigest()
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.object(precision, 'SOURCE_ARCHIVE_SHA256', precision.SOURCE_ARCHIVE_SHA256))
    stack.enter_context(patch.object(precision, 'download_source', side_effect=download))
    return stack


class CandidateTools:
    """External Go/Docker substitute: observable files and ownership state."""
    def __init__(self, fail_tests=False, owner='aiv-e2e-test', image_present=True, remove_fails=False,
                 fail_image=False):
        self.env = {'PATH': '/tools', 'GOOS': 'windows', 'GOARCH': 'arm64'}
        self.fail_tests, self.owner = fail_tests, owner
        self.image_built = self.image_removed = self.published = False
        self.image_present, self.remove_fails = image_present, remove_fails
        self.native_test_platform = self.build_platform = None
        self.fail_image = fail_image
        self.binary_mode_at_image_build = self.dockerfile_at_image_build = None

    def run(self, label, command, **kwargs):
        output = ''
        if command == ['go', 'version']:
            output = 'go version go1.25.7 linux/amd64\n'
        elif command[:2] == ['go', 'test']:
            self.native_test_platform = tuple(kwargs['env'].get(key, '') for key in ('GOOS', 'GOARCH'))
            if self.fail_tests:
                raise RuntimeError('precision regression failed')
        elif command[:2] == ['go', 'build']:
            self.build_platform = tuple(kwargs['env'][key] for key in ('GOOS', 'GOARCH', 'CGO_ENABLED'))
            Path(command[command.index('-o') + 1]).write_bytes(b'tested executable')
        elif command[:2] == ['docker', 'build']:
            context = Path(command[-1])
            self.binary_mode_at_image_build = stat.S_IMODE((context/'signoz').stat().st_mode)
            self.dockerfile_at_image_build = (context/'Dockerfile').read_text()
            if self.fail_image:
                raise RuntimeError('image build failed')
            self.image_built = True
            self.image_present = True
        elif command[:3] == ['docker', 'image', 'ls']:
            output = 'image-id' if self.image_present else ''
        elif command[:3] == ['docker', 'image', 'inspect']:
            if not self.image_present:
                raise RuntimeError('image not found')
            output = self.owner if 'Labels' in command[command.index('--format') + 1] else 'sha256:' + 'b'*64
        elif command[:3] == ['docker', 'image', 'rm']:
            self.image_removed = True
            if not self.remove_fails:
                self.image_present = False
        elif command[:2] in (['docker', 'push'], ['git', 'push']):
            self.published = True
        return SimpleNamespace(returncode=0, stdout=output, stderr='')


class BuildResourceTools(CandidateTools):
    """Go substitute writes real cache files selected by the child environment."""
    def __init__(self, external):
        super().__init__()
        for variable, folder in (('GOCACHE', 'build'), ('GOMODCACHE', 'modules')):
            directory = external/folder
            directory.mkdir(parents=True)
            (directory/'keep').write_text('existing runner cache')
            self.env[variable] = str(directory)
        self.cache_files = []
        self.private_cache_inputs_existed_at_image_build = False

    def run(self, label, command, **kwargs):
        if command[:2] in (['go', 'test'], ['go', 'build']):
            for variable in ('GOCACHE', 'GOMODCACHE'):
                directory = Path(kwargs['env'][variable])
                directory.mkdir(parents=True, exist_ok=True)
                path = directory/('receipt-' + command[1])
                path.write_text('real cache input')
                self.cache_files.append(path)
        if command[:2] == ['docker', 'build']:
            context = Path(command[-1])
            self.private_cache_inputs_existed_at_image_build = all(
                path.exists() and path.is_relative_to(context.parent) for path in self.cache_files)
        return super().run(label, command, **kwargs)


if __name__ == '__main__':
    unittest.main()
