#!/bin/sh
# Release packaging replaces this marker with github.repository. The source
# script also accepts --repository OWNER/REPO for private/forked distributions.
set -eu
repository=${AIV_REPOSITORY:-__AIV_RELEASE_REPOSITORY__}
version=latest
destination=${AIV_INSTALL_DIR:-"$HOME/.local/bin"}
modify_path=true
die() { printf '%s\n' "$*" >&2; exit 1; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --version|--repository|--install-dir)
      [ "$#" -ge 2 ] || die "$1 requires a value"
      case "$1" in
        --version) version=$2 ;;
        --repository) repository=$2 ;;
        --install-dir) destination=$2 ;;
      esac
      shift 2 ;;
    --no-modify-path) modify_path=false; shift ;;
    --help|-h)
      printf '%s\n' 'usage: sh install-aiv.sh [--version vX.Y.Z|TAG] [--repository OWNER/REPO] [--install-dir DIR] [--no-modify-path]'
      printf '%s\n' 'By default, configure PATH for new Bash or zsh terminals using $SHELL.'
      printf '%s\n' 'zsh uses exported ZDOTDIR, or HOME when unset. Other shells require manual PATH setup.'
      exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
case "$destination" in
  /*) ;;
  *) destination="$(pwd)/$destination" ;;
esac
case "$repository" in __*) die 'Source installer requires --repository OWNER/REPO (release installers have this preconfigured).' ;; esac
case "$repository" in *[!a-zA-Z0-9_./-]*|/*|*/|*..*) die 'Invalid release repository' ;; esac
case "$repository" in */*) ;; *) die 'Repository must be OWNER/REPO' ;; esac
case "$version" in *[!a-zA-Z0-9._-]*|'') die 'Invalid version' ;; esac
case "$(uname -s)" in Linux) platform=linux ;; Darwin) platform=darwin ;; *) die 'Supported platforms: Linux and macOS' ;; esac
case "$(uname -m)" in x86_64|amd64) arch=x64 ;; arm64|aarch64) arch=arm64 ;; *) die 'Supported architectures: x64 and arm64' ;; esac
command -v curl >/dev/null 2>&1 || die 'curl is required'
command -v tar >/dev/null 2>&1 || die 'tar is required'
if command -v sha256sum >/dev/null 2>&1; then checksum=sha256sum
elif command -v shasum >/dev/null 2>&1; then checksum=shasum
else die 'sha256sum or shasum is required'; fi
if [ "$version" = latest ]; then base="https://github.com/$repository/releases/latest/download"
else
  # Prefix numeric releases only; named tags such as dev are used verbatim.
  case "$version" in v*) ;; [0-9]*) version="v$version" ;; esac
  base="https://github.com/$repository/releases/download/$version"
fi
archive="aiv-$platform-$arch.tar.gz"
temporary=$(mktemp -d)
staged=''
profile_staged=''
trap 'rm -rf "$temporary"; if [ -n "$staged" ]; then rm -f "$staged"; fi; if [ -n "$profile_staged" ]; then rm -f "$profile_staged"; fi' EXIT HUP INT TERM
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$base/$archive" -o "$temporary/$archive"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$base/SHA256SUMS" -o "$temporary/SHA256SUMS"
expected=$(awk -v file="$archive" '$2 == file { print $1; count++ } END { if (count != 1) exit 1 }' "$temporary/SHA256SUMS") || die 'Missing or duplicate checksum'
case "$expected" in *[!a-f0-9]*|'') die 'Invalid checksum' ;; esac
[ "${#expected}" -eq 64 ] || die 'Invalid SHA-256 length'
if [ "$checksum" = sha256sum ]; then actual=$(sha256sum "$temporary/$archive" | awk '{print $1}')
else actual=$(shasum -a 256 "$temporary/$archive" | awk '{print $1}'); fi
[ "$actual" = "$expected" ] || die 'SHA-256 mismatch; existing aiv was preserved'
# Extract only the regular executable, never arbitrary archive paths or links.
members=$(tar -tzf "$temporary/$archive") || die 'Cannot read CLI archive'
printf '%s\n' "$members" | awk '
  $0 == "aiv" { count++; next }
  $0 == "LICENSE" || $0 == "deploy/kubernetes/README.md" ||
  $0 == "deploy/kubernetes/dependencies/cert-manager.LICENSE" ||
  $0 == "deploy/kubernetes/dependencies/ob-operator.LICENSE" { next }
  { exit 1 }
  END { if (count != 1) exit 1 }
' || die 'Unexpected CLI archive contents'
case "$(tar -tvzf "$temporary/$archive" aiv)" in -*) ;; *) die 'Archive executable must be a regular file' ;; esac
tar -xzf "$temporary/$archive" -C "$temporary" aiv
chmod 755 "$temporary/aiv"
"$temporary/aiv" --version
mkdir -p "$destination"
staged=$(mktemp "$destination/.aiv-install.XXXXXX")
cp "$temporary/aiv" "$staged"
chmod 755 "$staged"
mv -f "$staged" "$destination/aiv"
staged=''
printf 'Installed aiv to %s/aiv\n' "$destination"
# Quote the literal directory for both startup files and the copyable command.
# Never turn path contents into expansions when a shell reads these lines.
quoted_destination=$(printf '%s' "$destination" | sed "s/'/'\\\\''/g")
path_begin='# >>> aiv CLI PATH >>>'
path_end='# <<< aiv CLI PATH <<<'
update_profile() {
  profile_file=$1
  # Keep dotfile-manager symlinks intact, updating their target atomically.
  profile_hops=0
  while [ -L "$profile_file" ]; do
    profile_hops=$((profile_hops + 1))
    [ "$profile_hops" -le 40 ] || return 1
    profile_link=$(readlink "$profile_file") || return 1
    case "$profile_link" in
      /*) profile_file=$profile_link ;;
      *) profile_file="$(dirname "$profile_file")/$profile_link" ;;
    esac
  done
  if [ -e "$profile_file" ]; then
    [ -f "$profile_file" ] && [ -r "$profile_file" ] && [ -w "$profile_file" ] || return 1
    profile_input=$profile_file
  else
    profile_input=/dev/null
  fi
  mkdir -p "$(dirname "$profile_file")" || return 1
  profile_staged=$(mktemp "$profile_file.aiv-path.XXXXXX") || return 1
  if [ "$profile_input" != /dev/null ]; then
    cp -p "$profile_file" "$profile_staged" || return 1
  fi
  # Replace only our complete managed block; malformed markers require manual
  # repair instead of risking removal of the user's remaining configuration.
  awk -v begin="$path_begin" -v end="$path_end" '
    $0 == begin { if (inside || seen) exit 1; inside=1; seen=1; next }
    $0 == end { if (!inside) exit 1; inside=0; next }
    !inside { print }
    END { if (inside) exit 1 }
  ' "$profile_input" > "$profile_staged" || return 1
  printf '%s\ncase ":$PATH:" in\n  *:%s:*) ;;\n  *) export PATH=%s:"$PATH" ;;\nesac\n%s\n' \
    "$path_begin" "'$quoted_destination'" "'$quoted_destination'" "$path_end" >> "$profile_staged" || return 1
  mv -f "$profile_staged" "$profile_file" || return 1
  profile_staged=''
}
path_status=0
if [ "$modify_path" = true ]; then
  login_shell=${SHELL:-}
  case "${login_shell##*/}" in
    bash)
      startup_first="$HOME/.bashrc"
      startup_second="$HOME/.bash_profile"
      # Bash reads only the first readable login file. Do not create a new
      # .bash_profile that would hide an existing .bash_login or .profile.
      for candidate in .bash_profile .bash_login .profile; do
        if [ -f "$HOME/$candidate" ] && [ -r "$HOME/$candidate" ]; then
          startup_second="$HOME/$candidate"
          break
        fi
      done
      ;;
    zsh)
      zsh_home=${ZDOTDIR:-$HOME}
      startup_first="$zsh_home/.zshrc"
      startup_second="$zsh_home/.zprofile"
      ;;
    *)
      startup_first=''
      printf '%s\n' 'Shell not recognized as Bash or zsh; configure PATH manually for new terminals.'
      ;;
  esac
  if [ -n "$startup_first" ]; then
    if update_profile "$startup_first" && update_profile "$startup_second"; then
      printf 'Configured PATH for new terminals in %s and %s\n' "$startup_first" "$startup_second"
    else
      printf '%s\n' 'aiv is installed, but PATH setup failed; repair your shell startup files manually and rerun the installer.' >&2
      path_status=1
    fi
  fi
else
  printf '%s\n' 'Automatic PATH setup disabled; configure PATH manually for new terminals.'
fi
case ":$PATH:" in
  *":$destination:"*) ;;
  *)
    printf '%s\n' 'To use aiv in this terminal (Bash, zsh or another POSIX-compatible shell), run:'
    printf 'export PATH=%s:"$PATH"\n' "'$quoted_destination'"
    ;;
esac
printf 'Verify the installation:\ncommand -v aiv\naiv --version\n'
exit "$path_status"
