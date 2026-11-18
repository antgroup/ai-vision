"""Build an unpublished, run-owned SigNoz candidate for precise OTLP readback.

The official base image stays pinned. Only the tested native query-plane fix is
overlaid; no image or source branch is published by this acceptance harness.
"""
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
from urllib.request import urlopen


SOURCE_COMMIT = '1deab460854209ca0b18094d10128dcd0598aea9'
SOURCE_URL = 'https://codeload.github.com/ob-labs/signoz/tar.gz/' + SOURCE_COMMIT
SOURCE_ARCHIVE_SHA256 = '25107e8b85285b1e4dd9475e291dead2fb5823c1eec66525b847e2178d1fb912'
PATCH_SHA256 = '79456f9e1b34bc8750b7c720f7e86a7e8847dd2c6c02ae50f3b969cba25ba656'


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path, expected):
    if file_sha256(path) != expected:
        raise ValueError('SigNoz source archive checksum mismatch')


def extract_source(archive, directory):
    root = 'signoz-' + SOURCE_COMMIT
    with tarfile.open(archive, 'r:gz') as packed:
        for item in packed.getmembers():
            parts = PurePosixPath(item.name).parts
            if not parts or parts[0] != root or '..' in parts or item.isdev():
                raise ValueError('SigNoz archive has an unexpected source path')
        packed.extractall(directory, filter='data')
    source = Path(directory)/root
    if not (source/'go.mod').is_file():
        raise ValueError('SigNoz source archive has no go.mod')
    return source


def validate_go_version(output):
    if not re.fullmatch(r'go version go1\.25\.7 \S+\s*', output):
        raise ValueError('SigNoz candidate requires exact Go 1.25.7')
    return '1.25.7'


def build_environment(source, *, cross_compile):
    env = {key: value for key, value in source.items()
           if key not in ('GOOS', 'GOARCH', 'CGO_ENABLED', 'GOFLAGS')}
    env.update(GOTOOLCHAIN='local', GOMAXPROCS='2', CGO_ENABLED='0', GOFLAGS='-modcacherw')
    if cross_compile:
        env.update(GOOS='linux', GOARCH='amd64')
    return env


def download_source(path):
    with urlopen(SOURCE_URL, timeout=60) as response, Path(path).open('wb') as stream:
        shutil.copyfileobj(response, stream)


def build_candidate(smoke, root, runtime, namespace, base_image):
    if not re.fullmatch(r'ghcr\.io/ob-labs/(?:signoz-ob|signoz)@sha256:[0-9a-f]{64}', base_image):
        raise ValueError('SigNoz candidate requires an explicit official base digest')
    if not re.fullmatch(r'aiv-e2e-[a-z0-9-]+', namespace):
        raise ValueError('Unexpected candidate namespace')
    patch = Path(root)/'fixtures/signoz-native-nanos.patch'
    if file_sha256(patch) != PATCH_SHA256:
        raise ValueError('SigNoz precision patch checksum mismatch')
    work = Path(runtime)/'signoz-precision'
    work.mkdir()
    try:
        archive = work/'source.tar.gz'
        download_source(archive)
        verify_archive(archive, SOURCE_ARCHIVE_SHA256)
        source = extract_source(archive, work/'source')
        temporary = work/'go-tmp'
        temporary.mkdir()
        # Native tests and the cross-build share only this run's caches. Keep
        # modules writable so an unprivileged runner can reclaim them before
        # OceanBase needs the same disk; no runner/global cache is pruned.
        inputs = dict(smoke.env, GOCACHE=str(work/'go-build-cache'),
                      GOMODCACHE=str(work/'go-module-cache'), GOTMPDIR=str(temporary))
        native = build_environment(inputs, cross_compile=False)
        version = smoke.run('signoz-go-version', ['go', 'version'], env=native)
        validate_go_version(version.stdout)
        smoke.run('signoz-patch-check', ['git', 'apply', '--check', str(patch)], cwd=source)
        smoke.run('signoz-patch', ['git', 'apply', str(patch)], cwd=source)
        smoke.run('signoz-precision-tests', ['go', 'test', '-v', '-p', '2', '-count=1',
                  './pkg/modules/aivision', '-run', '^TestQueryRows'], cwd=source, env=native, timeout=1200)
        context = work/'image'
        context.mkdir()
        binary = context/'signoz'
        smoke.run('signoz-build', ['go', 'build', '-p', '2', '-tags', 'timetzdata', '-buildvcs=false',
                  '-o', str(binary), './cmd/community'], cwd=source,
                  env=build_environment(inputs, cross_compile=True), timeout=1200)
        if not binary.is_file() or binary.stat().st_size == 0:
            raise ValueError('SigNoz candidate build produced no binary')
        binary.chmod(0o755)
        binary_sha256 = file_sha256(binary)
        image = 'aivision-signoz-e2e:' + namespace
        # Plain COPY preserves this executable's mode with both Docker's
        # legacy builder (Colima) and BuildKit (Linux hosted runners).
        (context/'Dockerfile').write_text('FROM ' + base_image + '\nCOPY signoz /app/signoz\n', encoding='utf-8')
        smoke.run('signoz-candidate-image', ['docker', 'build', '--platform', 'linux/amd64',
                  '--label', 'aivision.framework-e2e.namespace=' + namespace, '--tag', image, str(context)], timeout=600)
        image_id = smoke.run('signoz-candidate-image-id',
                             ['docker', 'image', 'inspect', '--format', '{{.Id}}', image]).stdout.strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
            raise ValueError('Docker returned an invalid candidate image identity')
        result = {'image': image, 'image_id': image_id, 'official_base_image': base_image,
                  'upstream_repo': 'https://github.com/ob-labs/signoz', 'source_head': SOURCE_COMMIT,
                  'source_archive_url': SOURCE_URL, 'source_archive_sha256': SOURCE_ARCHIVE_SHA256,
                  'patch_sha256': PATCH_SHA256, 'binary_sha256': binary_sha256,
                  'precision_tests': 'TestQueryRowsPreservesOTLPJSONNumbersOnNativeWire / '
                                     'TestQueryRowsStillRejectsMalformedOrMultipleJSONDocuments / '
                                     'TestQueryRowsPreservesNumericEventTimestampProjection',
                  'build': {'go': '1.25.7', 'GOOS': 'linux', 'GOARCH': 'amd64',
                            'CGO_ENABLED': '0', 'tags': 'timetzdata', 'buildvcs': False},
                  'build_inputs_removed': True,
                  'official_fixed_image_published': False, 'candidate_published': False}
    finally:
        # Logs live outside work, and provenance was calculated before this
        # removal. Failure also reclaims only the directory created above.
        shutil.rmtree(work)
    return result


def remove_candidate(smoke, namespace, image):
    listing = ['docker', 'image', 'ls', '-q', '--filter', 'reference=' + image]
    if not smoke.run('signoz-candidate-find', listing).stdout.strip():
        return
    owner = smoke.run('signoz-candidate-owner', ['docker', 'image', 'inspect', '--format',
                      '{{ index .Config.Labels "aivision.framework-e2e.namespace" }}', image]).stdout.strip()
    if owner != namespace:
        raise ValueError('SigNoz candidate image belongs to another run')
    smoke.run('signoz-candidate-remove', ['docker', 'image', 'rm', image])
    if smoke.run('signoz-candidate-verify-removed', listing).stdout.strip():
        raise ValueError('Owned SigNoz candidate image still exists after removal')
