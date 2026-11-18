"""Read bounded static models.dev source data without running upstream code.

Provider model files own tariffs. Lab definitions supply identity/capabilities,
never billing prices. Legacy ``v1_alias`` providers are omitted as in v2.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import os
import re
import stat
import tarfile
import tomllib
import zlib
from decimal import Decimal
from pathlib import Path


SOURCE_URL = 'https://github.com/anomalyco/models.dev'
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_TOML_BYTES = 1024 * 1024
MAX_ALIAS_DEPTH = 32
_REVISION = re.compile(r'[0-9a-f]{40}\Z')
_LAB_ONLY = {'id', 'benchmarks', 'license', 'links', 'weights'}


class RepositoryCatalogError(ValueError):
    """The entire repository input is unsafe, malformed or unsupported."""


def _path_parts(name):
    if not isinstance(name, str) or not name or name.startswith('/') or '\\' in name:
        raise RepositoryCatalogError('Repository contains an unsafe path')
    parts = name.split('/')
    if '..' in parts or any(ord(char) < 32 for char in name):
        raise RepositoryCatalogError('Repository contains an unsafe path')
    normalized = tuple(part for part in parts if part and part != '.')
    if not normalized:
        raise RepositoryCatalogError('Repository contains an unsafe path')
    return normalized


def _kind(parts):
    if not parts or not parts[-1].endswith('.toml'):
        return None
    if parts[0] == 'models' and len(parts) >= 3:
        return 'base'
    if parts[0] == 'providers':
        if len(parts) == 3 and parts[2] == 'provider.toml':
            return 'provider'
        if len(parts) >= 4 and parts[2] == 'models':
            return 'model'
    return None


def _alias_target(parts, link):
    """Normalize an authored model link in a virtual, repository-relative tree."""
    if _kind(parts) != 'model' or not isinstance(link, str) or not link or link.startswith('/') or '\\' in link or any(ord(char) < 32 for char in link):
        raise RepositoryCatalogError('Repository model alias path is unsafe')
    target = list(parts[:-1])
    for part in link.split('/'):
        if part in {'', '.'}:
            continue
        if part == '..':
            if not target:
                raise RepositoryCatalogError('Repository model alias escapes the repository')
            target.pop()
        else:
            target.append(part)
    if _kind(target) != 'model':
        raise RepositoryCatalogError('Repository aliases must target provider model files')
    return '/'.join(target)


def _resolve_aliases(material, aliases):
    def resolve(name, active):
        if len(active) >= MAX_ALIAS_DEPTH or name in active:
            raise RepositoryCatalogError('Repository model alias cycle or depth limit exceeded')
        if name in aliases:
            return resolve(aliases[name], active | {name})
        if name not in material or _kind(name.split('/')) != 'model':
            raise RepositoryCatalogError('Repository model alias target is missing')
        return material[name]

    # Count logical bytes too, so a small alias fan-out cannot expand into an
    # unbounded catalog while each TOML document is subsequently parsed.
    logical_bytes = sum(len(content) for content in material.values())
    for name in aliases:
        content = resolve(name, set())
        logical_bytes += len(content)
        if logical_bytes > MAX_EXPANDED_BYTES:
            raise RepositoryCatalogError('Repository aliases exceed the expanded size limit')
        material[name] = content


def catalog_from_archive(body: bytes, expected_revision: str | None = None):
    """Parse a GitHub tar.gz snapshot whose global PAX comment pins its commit."""
    if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_ARCHIVE_BYTES:
        raise RepositoryCatalogError('Repository archive exceeds the compressed size limit')
    if expected_revision is not None and (not isinstance(expected_revision, str) or not _REVISION.fullmatch(expected_revision)):
        raise RepositoryCatalogError('Repository revision must be a full commit SHA')
    try:
        # Bound the actual decompressed stream, including tar headers/PAX data,
        # rather than trusting member sizes supplied by the archive itself.
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
            expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
        if len(expanded) > MAX_EXPANDED_BYTES:
            raise RepositoryCatalogError('Repository archive exceeds the expanded size limit')
        material, aliases = {}, {}
        seen = set()
        roots = set()
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
            revision = archive.pax_headers.get('comment')
            if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
                raise RepositoryCatalogError('Repository archive has no verified commit SHA')
            if expected_revision is not None and revision != expected_revision:
                raise RepositoryCatalogError('Repository archive commit does not match the requested revision')
            for count, member in enumerate(archive, start=1):
                if count > MAX_MEMBERS:
                    raise RepositoryCatalogError('Repository member limit exceeded')
                parts = _path_parts(member.name)
                if parts in seen:
                    raise RepositoryCatalogError('Repository contains duplicate paths')
                seen.add(parts)
                roots.add(parts[0])
                if member.isdir():
                    continue
                if len(parts) < 2:
                    raise RepositoryCatalogError('Repository archive must have a single root directory')
                relative = parts[1:]
                if member.issym():
                    aliases['/'.join(relative)] = _alias_target(relative, member.linkname)
                    continue
                if not member.isfile():
                    raise RepositoryCatalogError('Repository hard links and special members are not supported')
                if _kind(relative) is None:
                    continue
                if member.size > MAX_TOML_BYTES:
                    raise RepositoryCatalogError('Repository TOML file exceeds the size limit')
                stream = archive.extractfile(member)
                if stream is None:
                    raise RepositoryCatalogError('Repository file cannot be read')
                with stream:
                    content = stream.read(MAX_TOML_BYTES + 1)
                if len(content) > MAX_TOML_BYTES:
                    raise RepositoryCatalogError('Repository TOML file exceeds the size limit')
                material['/'.join(relative)] = content
            if len(roots) != 1:
                raise RepositoryCatalogError('Repository archive must have a single root directory')
        _resolve_aliases(material, aliases)
        catalog = _catalog(material)
    except RepositoryCatalogError:
        raise
    except (OSError, EOFError, tarfile.TarError, zlib.error, ValueError, UnicodeError, RecursionError) as exc:
        raise RepositoryCatalogError('Repository archive is invalid') from exc
    return catalog, {
        'sourceType': 'repository', 'sourceUrl': SOURCE_URL + '/tree/' + revision,
        'sourceRevision': revision,
    }


def catalog_from_directory(path):
    """Read only static models/providers trees; hash content, never trust HEAD."""
    material, aliases = {}, {}
    total_bytes = 0
    count = 0
    try:
        root = Path(path)
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise RepositoryCatalogError('Repository path must be a regular directory')
        pending = [root / 'models', root / 'providers']
        while pending:
            directory = pending.pop()
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise RepositoryCatalogError('Repository links and special entries are not supported')
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_MEMBERS:
                        raise RepositoryCatalogError('Repository member limit exceeded')
                    relative = Path(entry.path).relative_to(root).as_posix()
                    parts = _path_parts(relative)
                    metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(Path(entry.path))
                        continue
                    if stat.S_ISLNK(metadata.st_mode):
                        aliases['/'.join(parts)] = _alias_target(parts, os.readlink(entry.path))
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        raise RepositoryCatalogError('Repository links and special entries are not supported')
                    total_bytes += metadata.st_size
                    if total_bytes > MAX_EXPANDED_BYTES:
                        raise RepositoryCatalogError('Repository exceeds the expanded size limit')
                    if _kind(parts) is None:
                        continue
                    if metadata.st_size > MAX_TOML_BYTES:
                        raise RepositoryCatalogError('Repository TOML file exceeds the size limit')
                    descriptor = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, 'rb') as source:
                        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                            raise RepositoryCatalogError('Repository file must be regular')
                        content = source.read(MAX_TOML_BYTES + 1)
                    if len(content) > MAX_TOML_BYTES:
                        raise RepositoryCatalogError('Repository TOML file exceeds the size limit')
                    # Account for a regular file growing between stat and read.
                    total_bytes += max(0, len(content) - metadata.st_size)
                    if total_bytes > MAX_EXPANDED_BYTES:
                        raise RepositoryCatalogError('Repository exceeds the expanded size limit')
                    material['/'.join(parts)] = content
        _resolve_aliases(material, aliases)
        catalog = _catalog(material)
    except RepositoryCatalogError:
        raise
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        raise RepositoryCatalogError('Repository directory cannot be read') from exc
    digest = hashlib.sha256()
    for name, content in sorted(material.items()):
        digest.update(name.encode('utf-8') + b'\0')
        digest.update(len(content).to_bytes(8, 'big'))
        digest.update(content)
    for name, target in sorted(aliases.items()):
        digest.update(b'alias\0' + name.encode('utf-8') + b'\0' + target.encode('utf-8') + b'\0')
    return catalog, {
        'sourceType': 'local_repository', 'sourceUrl': None,
        'sourceRevision': 'sha256:' + digest.hexdigest(),
    }


def _json_safe(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise RepositoryCatalogError('Repository contains a non-finite number')
        # Scientific notation preserves precision without allocating an attacker
        # supplied exponent's worth of zeroes before tariff validation.
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, bool)):
        return value
    raise RepositoryCatalogError('Repository contains an unsupported TOML value')


def _merge(base, overrides):
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _omit(target, paths):
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path or any(not part for part in path.split('.')) for path in paths):
        raise RepositoryCatalogError('Repository base_model_omit must contain dotted field paths')
    for path in paths:
        parts = path.split('.')
        current = target
        parents = []
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                break
            parents.append((current, part))
            current = child
        else:
            if parts[-1] not in current:
                continue
            del current[parts[-1]]
            for parent, key in reversed(parents):
                if parent[key]:
                    break
                del parent[key]


def _catalog(material):
    bases, providers, models = {}, {}, {}
    try:
        for name, content in sorted(material.items()):
            parts = _path_parts(name)
            kind = _kind(parts)
            raw = _json_safe(tomllib.loads(content.decode('utf-8'), parse_float=Decimal))
            if kind != 'provider' and (type(raw.get('schema')) is not int or raw['schema'] != 2):
                raise RepositoryCatalogError('Repository model schema is unsupported; expected schema 2')
            if kind == 'base':
                if 'cost' in raw:
                    raise RepositoryCatalogError('Repository lab metadata cannot define billing prices')
                bases['/'.join(parts[1:])[:-5]] = raw
            elif kind == 'provider':
                if 'schema' in raw and raw['schema'] != 2:
                    raise RepositoryCatalogError('Repository provider schema is unsupported')
                providers[parts[1]] = raw
            elif kind == 'model':
                models.setdefault(parts[1], {})['/'.join(parts[3:])[:-5]] = raw
        if not bases or not providers or not models:
            raise RepositoryCatalogError('Repository must contain lab and provider model files')
        if set(models) - set(providers):
            raise RepositoryCatalogError('Repository model has no provider metadata')
        resolved, active = {}, set()

        def resolve(raw):
            reference = raw.get('base_model')
            overrides = {key: value for key, value in raw.items() if key not in {'base_model', 'base_model_omit'}}
            if reference is None:
                if 'base_model' in raw or 'base_model_omit' in raw:
                    raise RepositoryCatalogError('Repository inheritance requires a base_model')
                return copy.deepcopy(overrides)
            if not isinstance(reference, str) or '/'.join(_path_parts(reference)) != reference or len(reference.split('/')) < 2:
                raise RepositoryCatalogError('Repository base_model path is invalid')
            if reference not in bases:
                raise RepositoryCatalogError('Repository base_model cannot be resolved')
            base = resolve_base(reference)
            merged = _merge({key: value for key, value in base.items() if key not in _LAB_ONLY}, overrides)
            _omit(merged, raw.get('base_model_omit', []))
            return merged

        def resolve_base(reference):
            if reference in active:
                raise RepositoryCatalogError('Repository base_model inheritance contains a cycle')
            if reference not in resolved:
                active.add(reference)
                resolved[reference] = resolve(bases[reference])
                active.remove(reference)
            return resolved[reference]

        # Validate every authored file, even unused lab definitions and aliases.
        for reference in bases:
            resolve_base(reference)
        catalog = {}
        for provider_id, raw_provider in providers.items():
            provider_models = {}
            for model_id, raw_model in models.get(provider_id, {}).items():
                model = resolve(raw_model)
                model.update(id=model_id, schema=2, canonical_model_id=raw_model.get('base_model', model_id))
                provider_models[model_id] = model
            if 'v1_alias' in raw_provider:
                if provider_models or not isinstance(raw_provider['v1_alias'], dict) or not isinstance(raw_provider['v1_alias'].get('provider'), str):
                    raise RepositoryCatalogError('Repository legacy provider alias is invalid')
                continue
            if not provider_models:
                raise RepositoryCatalogError('Repository provider contains no models')
            catalog[provider_id] = {**raw_provider, 'id': provider_id, 'models': provider_models}
        if not catalog:
            raise RepositoryCatalogError('Repository contains no billable provider models')
        return catalog
    except RepositoryCatalogError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise RepositoryCatalogError('Repository TOML data is invalid') from exc
