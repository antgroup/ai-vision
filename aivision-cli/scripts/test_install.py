import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


class InstallTests(unittest.TestCase):
    def test_path_guidance_runs_the_installed_cli_in_a_fresh_shell(self):
        installer = Path(__file__).with_name('install.sh')
        cases = [
            ('default', None, False),
            ('already-on-path', None, True),
            ('spaces', 'custom bin', False),
            ('single-quote', "custom'bin", False),
            ('shell-metacharacters', 'custom $HOME $(touch dollar-pwned) `touch backtick-pwned`', False),
            ('relative', 'relative bin', False),
        ]
        shells = [('/bin/sh', ['-c'])]
        if Path('/bin/bash').exists():
            shells.append(('/bin/bash', ['--noprofile', '--norc', '-c']))
        if Path('/bin/zsh').exists():
            shells.append(('/bin/zsh', ['-f', '-c']))
        for name, custom_directory, already_on_path in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temp:
                # macOS aliases /var to /private/var; match the subprocess cwd.
                root = Path(temp).resolve()
                home = root / 'home'
                tools = root / 'tools'
                tools.mkdir()
                executable = b'#!/bin/sh\nprintf "aiv test-version\\n"\n'
                archive = root / 'aiv-linux-x64.tar.gz'
                with tarfile.open(archive, 'w:gz') as package:
                    info = tarfile.TarInfo('aiv')
                    info.size, info.mode = len(executable), 0o755
                    package.addfile(info, io.BytesIO(executable))
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                (root / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n')
                (tools / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
                (tools / 'curl').write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in https:*) url=$1;; -o) shift; output=$1;; esac
  shift
done
cp "$FIXTURE/${url##*/}" "$output"
''')
                for tool in tools.iterdir():
                    tool.chmod(0o755)
                destination = root / custom_directory if custom_directory else home / '.local/bin'
                path = f'{tools}:/usr/bin:/bin'
                if already_on_path:
                    path = str(destination) + os.pathsep + path
                env = {'HOME': str(home), 'PATH': path, 'FIXTURE': str(root)}
                released = root / 'install-aiv.sh'
                released.write_text(installer.read_text().replace('__AIV_RELEASE_REPOSITORY__', 'example/aiv'))
                cmd = ['/bin/sh', str(released), '--no-modify-path']
                if custom_directory:
                    cmd.extend(['--install-dir', custom_directory if name == 'relative' else str(destination)])
                result = subprocess.run(cmd, env=env, cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((destination / 'aiv').read_bytes(), executable)
                commands = [line for line in result.stdout.splitlines() if line.startswith('export PATH=')]
                self.assertEqual(len(commands), 0 if already_on_path else 1, result.stdout)
                self.assertIn('aiv --version', result.stdout.splitlines())
                other_directory = root / 'elsewhere'
                other_directory.mkdir()
                for shell, flags in shells:
                    with self.subTest(shell=shell):
                        script = '\n'.join(commands + ['cd "$CHECK_DIRECTORY"', 'command -v aiv', 'aiv --version'])
                        check_env = {**env, 'CHECK_DIRECTORY': str(other_directory)}
                        check = subprocess.run([shell, *flags, script], env=check_env, cwd=root,
                                               capture_output=True, text=True)
                        self.assertEqual(check.returncode, 0, check.stderr)
                        self.assertEqual(check.stdout.splitlines(), [str(destination / 'aiv'), 'aiv test-version'])
                self.assertFalse((root / 'dollar-pwned').exists())
                self.assertFalse((root / 'backtick-pwned').exists())
                for profile in ['.zshrc', '.zprofile', '.bashrc', '.bash_profile', '.profile']:
                    self.assertFalse((home / profile).exists())

    def path_fixture(self, root, shell):
        home, tools = root / 'home', root / 'tools'
        home.mkdir()
        tools.mkdir()
        executable = b'#!/bin/sh\nprintf "aiv test-version\\n"\n'
        archive = root / 'aiv-linux-x64.tar.gz'
        with tarfile.open(archive, 'w:gz') as package:
            info = tarfile.TarInfo('aiv')
            info.size, info.mode = len(executable), 0o755
            package.addfile(info, io.BytesIO(executable))
        (root / 'SHA256SUMS').write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
        (tools / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        (tools / 'curl').write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in https:*) url=$1;; -o) shift; output=$1;; esac
  shift
done
cp "$FIXTURE/${url##*/}" "$output"
''')
        for tool in tools.iterdir():
            tool.chmod(0o755)
        released = root / 'install-aiv.sh'
        released.write_text(Path(__file__).with_name('install.sh').read_text().replace(
            '__AIV_RELEASE_REPOSITORY__', 'example/aiv'))
        env = {'HOME': str(home), 'PATH': f'{tools}:/usr/bin:/bin', 'FIXTURE': str(root),
               'SHELL': shell, 'TERM': 'dumb'}
        return home, released, env

    def assert_fresh_shell_finds_cli(self, root, shell, env, destination):
        # Do not apply the installer's printed export: startup files must work.
        for flags in ['-ic', '-lc', '-lic']:
            with self.subTest(shell=shell, flags=flags):
                check = subprocess.run([shell, flags, 'command -v aiv; aiv --version'],
                                       env=env, cwd=root, capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertEqual(check.stdout.splitlines(), [str(destination / 'aiv'), 'aiv test-version'])

    def test_bash_startup_files_make_cli_callable_without_shadowing_login_config(self):
        for login_file in [None, '.bash_profile', '.bash_login', '.profile']:
            with self.subTest(login_file=login_file), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                home, released, env = self.path_fixture(root, '/bin/bash')
                original = "export AIV_USER_SETTING='preserved'"  # Deliberately no final newline.
                (home / '.bashrc').write_text(original)
                if login_file:
                    (home / login_file).write_text(original)
                install_env = env
                if login_file == '.bash_profile':
                    # An inherited PATH entry must not suppress durable setup.
                    install_env = {**env, 'PATH': f'{home}/.local/bin:{env["PATH"]}'}
                result = subprocess.run(['/bin/sh', str(released)], env=install_env, cwd=root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_fresh_shell_finds_cli(root, '/bin/bash', env, home / '.local/bin')
                self.assertTrue((home / '.bashrc').read_text().startswith(original))
                if login_file:
                    self.assertTrue((home / login_file).read_text().startswith(original))
                if login_file in ['.bash_login', '.profile']:
                    self.assertFalse((home / '.bash_profile').exists())

    @unittest.skipUnless(Path('/bin/zsh').exists(), 'zsh is unavailable')
    def test_zsh_startup_files_honor_zdotdir_and_literal_install_paths(self):
        for custom in [None, "custom bin' $HOME $(touch dollar-pwned) `touch backtick-pwned`"]:
            with self.subTest(custom=custom), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                home, released, env = self.path_fixture(root, '/bin/zsh')
                dotdir = root / "zsh config' $HOME" if custom else home
                if custom:
                    dotdir.mkdir()
                    env['ZDOTDIR'] = str(dotdir)
                original = "export AIV_USER_SETTING='preserved'\n"
                (dotdir / '.zshrc').write_text(original)
                (dotdir / '.zprofile').write_text(original)
                destination = root / custom if custom else home / '.local/bin'
                cmd = ['/bin/sh', str(released)]
                if custom:
                    cmd += ['--install-dir', str(destination)]
                result = subprocess.run(cmd, env=env, cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_fresh_shell_finds_cli(root, '/bin/zsh', env, destination)
                for name in ['.zshrc', '.zprofile']:
                    self.assertTrue((dotdir / name).read_text().startswith(original))
                    if custom:
                        self.assertFalse((home / name).exists())
                self.assertFalse((root / 'dollar-pwned').exists())
                self.assertFalse((root / 'backtick-pwned').exists())

    def test_repeated_install_and_changed_directory_keep_one_effective_path_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home, released, env = self.path_fixture(root, '/bin/bash')
            # Login shells commonly source .bashrc; both hooks must be idempotent.
            (home / '.bash_profile').write_text('. "$HOME/.bashrc"\n')
            (home / '.bashrc').write_text('# user settings\n')
            profiles_after_first_install = None
            for index, destination in enumerate([home / '.local/bin', home / '.local/bin', root / "new bin' $HOME"]):
                result = subprocess.run(['/bin/sh', str(released), '--install-dir', str(destination)],
                                        env=env, cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_fresh_shell_finds_cli(root, '/bin/bash', env, destination)
                check = subprocess.run(['/bin/bash', '-lic', 'printf "%s\\n" "$PATH"'],
                                       env=env, cwd=root, capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertEqual(check.stdout.strip().split(os.pathsep).count(str(destination)), 1)
                profiles = [(home / name).read_bytes() for name in ['.bashrc', '.bash_profile']]
                if index == 0:
                    profiles_after_first_install = profiles
                if index == 1:
                    self.assertEqual(profiles, profiles_after_first_install)
            self.assertTrue((home / '.bash_profile').read_text().startswith('. "$HOME/.bashrc"\n'))

    def test_opt_out_leaves_startup_files_unchanged_and_provides_current_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home, released, env = self.path_fixture(root, '/bin/bash')
            original = '# user config without final newline'
            (home / '.bashrc').write_text(original)
            result = subprocess.run(['/bin/sh', str(released), '--no-modify-path'], env=env,
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((home / '.bashrc').read_text(), original)
            self.assertFalse((home / '.bash_profile').exists())
            self.assertIn('export PATH=', result.stdout)

    def test_unknown_shell_requests_manual_setup_without_creating_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home, released, env = self.path_fixture(root, '/usr/bin/fish')
            result = subprocess.run(['/bin/sh', str(released)], env=env, cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('manual', (result.stdout + result.stderr).lower())
            self.assertFalse(any(home.glob('.*profile')))
            self.assertFalse(any(home.glob('.*rc')))

    def test_startup_configuration_failure_preserves_user_config_and_reports_manual_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home, released, env = self.path_fixture(root, '/bin/bash')
            (home / '.bashrc').mkdir()  # A deterministic write failure, including as root.
            original = '# login settings\n'
            (home / '.bash_profile').write_text(original)
            result = subprocess.run(['/bin/sh', str(released)], env=env, cwd=root,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('manual', (result.stdout + result.stderr).lower())
            self.assertIn('export PATH=', result.stdout)
            self.assertEqual((home / '.bash_profile').read_text(), original)
            self.assertTrue((home / '.local/bin/aiv').is_file())

    def test_symlinked_startup_files_keep_their_links_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home, released, env = self.path_fixture(root, '/bin/bash')
            configs = home / 'dotfiles'
            configs.mkdir()
            original = '# tracked dotfile\n'
            for name in ['.bashrc', '.bash_profile']:
                target = configs / name
                target.write_text(original)
                target.chmod(0o640)
                (home / name).symlink_to(Path('dotfiles') / name)
            result = subprocess.run(['/bin/sh', str(released)], env=env, cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_fresh_shell_finds_cli(root, '/bin/bash', env, home / '.local/bin')
            for name in ['.bashrc', '.bash_profile']:
                self.assertTrue((home / name).is_symlink())
                self.assertTrue((configs / name).read_text().startswith(original))
                self.assertEqual((configs / name).stat().st_mode & 0o777, 0o640)

    def test_malformed_managed_block_is_preserved_for_manual_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home, released, env = self.path_fixture(root, '/bin/bash')
            original = '# >>> aiv CLI PATH >>>\n# user configuration must not be lost\n'
            (home / '.bashrc').write_text(original)
            result = subprocess.run(['/bin/sh', str(released)], env=env, cwd=root,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('manual', (result.stdout + result.stderr).lower())
            self.assertEqual((home / '.bashrc').read_text(), original)
            self.assertFalse((home / '.bash_profile').exists())
            self.assertEqual(list(home.glob('*.aiv-path.*')), [])

    def test_platform_selection_version_pin_checksum_and_atomic_replacement(self):
        installer = Path(__file__).with_name('install.sh')
        for system, machine, target in [('Linux', 'x86_64', 'linux-x64'), ('Linux', 'aarch64', 'linux-arm64'),
                                         ('Darwin', 'x86_64', 'darwin-x64'), ('Darwin', 'arm64', 'darwin-arm64')]:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                tools = root / 'tools'
                tools.mkdir()
                executable = b'#!/bin/sh\nprintf "aiv test-version\\n"\n'
                archive = root / f'aiv-{target}.tar.gz'
                with tarfile.open(archive, 'w:gz') as package:
                    info = tarfile.TarInfo('aiv')
                    info.size, info.mode = len(executable), 0o755
                    package.addfile(info, io.BytesIO(executable))
                    for name in ['LICENSE', 'deploy/kubernetes/README.md',
                                 'deploy/kubernetes/dependencies/cert-manager.LICENSE',
                                 'deploy/kubernetes/dependencies/ob-operator.LICENSE']:
                        notice = tarfile.TarInfo(name)
                        notice.size = 7
                        package.addfile(notice, io.BytesIO(b'license'))
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                sums = root / 'SHA256SUMS'
                sums.write_text(f'{digest}  {archive.name}\n')
                (tools / 'uname').write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system};; -m) echo {machine};; esac\n')
                (tools / 'curl').write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in https:*) url=$1;; -o) shift; output=$1;; esac
  shift
done
printf '%s\\n' "$url" >> "$FIXTURE/requests"
cp "$FIXTURE/${url##*/}" "$output"
''')
                for tool in tools.iterdir():
                    tool.chmod(0o755)
                env = {'PATH': f'{tools}:/usr/bin:/bin', 'FIXTURE': str(root), 'HOME': str(root)}
                # Exercise the exact stamped release script, including its default repository.
                released = root / 'install-aiv.sh'
                released.write_text(installer.read_text().replace('__AIV_RELEASE_REPOSITORY__', 'example/aiv'))
                destination = root / '.local/bin/aiv'
                for version, path in [('latest', 'latest/download'), ('dev', 'download/dev'),
                                      ('1.2.3', 'download/v1.2.3'), ('v1.2.3', 'download/v1.2.3')]:
                    with self.subTest(version=version):
                        (root / 'requests').write_text('')
                        cmd = ['sh', str(released), '--version', version]
                        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(destination.read_bytes(), executable)
                        base = f'https://github.com/example/aiv/releases/{path}'
                        self.assertEqual((root / 'requests').read_text().splitlines(),
                                         [f'{base}/{archive.name}', f'{base}/SHA256SUMS'])
                destination.write_text('preserve previous executable')
                sums.write_text(f'{"0" * 64}  {archive.name}\n')
                result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SHA-256 mismatch', result.stderr)
                self.assertEqual(destination.read_text(), 'preserve previous executable')
                self.assertEqual(sorted(p.name for p in destination.parent.iterdir()), ['aiv'])
                for layout in ['legacy', 'unexpected-path', 'symlink']:
                    with tarfile.open(archive, 'w:gz') as package:
                        info = tarfile.TarInfo('aiv')
                        info.size, info.mode = len(executable), 0o755
                        if layout == 'symlink':
                            info.type, info.linkname, info.size = tarfile.SYMTYPE, '/bin/sh', 0
                            package.addfile(info)
                        else:
                            package.addfile(info, io.BytesIO(executable))
                        if layout == 'unexpected-path':
                            package.addfile(tarfile.TarInfo('../escape'))
                    sums.write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
                    destination.write_text('preserve previous executable')
                    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                    if layout == 'legacy':
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(destination.read_bytes(), executable)
                    else:
                        self.assertNotEqual(result.returncode, 0, layout)
                        self.assertEqual(destination.read_text(), 'preserve previous executable')


if __name__ == '__main__':
    unittest.main()
