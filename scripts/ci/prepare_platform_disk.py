"""Reclaim only the unused Android SDK on a disposable hosted Linux X64 VM.

The fixed SDK path is owned by actions/runner-images' install-android-sdk.sh:
https://github.com/actions/runner-images/blob/main/images/ubuntu/scripts/build/install-android-sdk.sh
No path override or cache/database pruning is supported.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


SDK_ROOT = Path('/usr/local/lib/android/sdk')
# Reserve room for image pulls, temporary builds and database startup.
FREE_DISK_RESERVE_BYTES = 64 * 1024**3


def prepare():
    required = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64'}
    if any(os.environ.get(key) != value for key, value in required.items()):
        return {'status': 'skipped', 'reason': 'runner_not_eligible'}
    result = {'status': 'failed', 'free_bytes_before': None, 'free_bytes_after': None,
              'sdk_bytes_removed': None, 'reclaimed_bytes': None}

    def failed(reason):
        return {**result, 'reason': reason}

    for key in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if key in os.environ and os.environ[key] != str(SDK_ROOT):
            return failed('unexpected_sdk_environment')
    try:
        sdk_stat = None
        # Refuse a substituted SDK root or ancestor. rm never follows contained
        # symlinks, and --one-file-system keeps nested mounts outside deletion.
        for path in (SDK_ROOT, *SDK_ROOT.parents):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(info.st_mode):
                return failed('unsafe_sdk_path')
            if path == SDK_ROOT:
                sdk_stat = info
        result['free_bytes_before'] = shutil.disk_usage('/').free
        if sdk_stat is None:
            result.update(status='absent', free_bytes_after=shutil.disk_usage('/').free,
                          sdk_bytes_removed=0, reclaimed_bytes=0)
            return result
        if os.path.ismount(SDK_ROOT) or sdk_stat.st_dev != os.stat('/').st_dev:
            return failed('unsafe_sdk_path')
    except OSError:
        return failed('filesystem_inspection_failed')
    if result['free_bytes_before'] >= FREE_DISK_RESERVE_BYTES:
        result.update(status='retained', reason='sufficient_disk_space',
                      free_bytes_after=result['free_bytes_before'],
                      sdk_bytes_removed=0, reclaimed_bytes=0)
        return result
    try:
        measured = subprocess.run(['du', '-sk', str(SDK_ROOT)], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120, check=False)
        fields = measured.stdout.split()
        if measured.returncode or not fields or not fields[0].isascii() or not fields[0].isdigit():
            return failed('sdk_size_measurement_failed')
        sdk_bytes = int(fields[0]) * 1024
    except (OSError, subprocess.TimeoutExpired):
        return failed('sdk_size_measurement_failed')
    try:
        removed = subprocess.run(['sudo', '-n', 'rm', '--recursive', '--force',
            '--one-file-system', '--', str(SDK_ROOT)], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=120, check=False)
        if removed.returncode:
            return failed('sdk_removal_failed')
    except (OSError, subprocess.TimeoutExpired):
        return failed('sdk_removal_failed')
    try:
        try:
            SDK_ROOT.lstat()
        except FileNotFoundError:
            pass
        else:
            return failed('sdk_still_present')
        result['free_bytes_after'] = shutil.disk_usage('/').free
    except OSError:
        return failed('cleanup_verification_failed')
    result.update(status='removed', sdk_bytes_removed=sdk_bytes,
                  reclaimed_bytes=max(0, result['free_bytes_after'] - result['free_bytes_before']))
    return result


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv):
        result = {'status': 'failed', 'reason': 'unexpected_arguments'}
    else:
        result = prepare()
    print(json.dumps(result, sort_keys=True))
    return int(result['status'] == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
