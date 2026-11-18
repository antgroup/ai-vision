"""Static repository fixtures exercise parsing, never upstream executable code."""
import io
import json
import tarfile
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.services import model_pricing_repository as repository


REVISION = '0123456789abcdef0123456789abcdef01234567'


def files():
    return {
        'models/lab/nested/model.toml': '''schema = 2
name = "Lab Model"
license = "lab license"
benchmarks = [{ name = "lab benchmark" }]
[limit]
context = 400000
output = 1000
[capabilities]
tools = { supported = true, choice = ["auto", "none"] }
temperature = true
''',
        'providers/host/provider.toml': 'name = "Host"\nenv = ["HOST_API_KEY"]\n',
        'providers/host/models/vendor/nested.toml': '''schema = 2
base_model = "lab/nested/model"
base_model_omit = ["limit.output", "capabilities.temperature"]
name = "Hosted Model"
[capabilities.tools]
choice = ["auto"]
[cost.text.input]
price = 0.123456789012345678
[cost.text.output]
price = 0
''',
        'providers/host/models/missing.toml': 'schema = 2\nname = "No tariff"\n',
    }


def archive(material=None, *, revision=REVISION, extra=()):
    output = io.BytesIO()
    headers = {'comment': revision} if revision is not None else {}
    with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.PAX_FORMAT, pax_headers=headers) as handle:
        for name, value in (files() if material is None else material).items():
            data = value.encode()
            member = tarfile.TarInfo('models.dev-fixture/' + name)
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))
        for member, data in extra:
            handle.addfile(member, io.BytesIO(data))
    return output.getvalue()


def write_files(directory, material=None):
    for name, body in (files() if material is None else material).items():
        target = Path(directory) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)


class RepositoryCatalogTests(SimpleTestCase):
    def test_static_inheritance_keeps_nested_ids_precise_prices_and_omits_lab_only_fields(self):
        catalog, provenance = repository.catalog_from_archive(archive(), expected_revision=REVISION)
        hosted = catalog['host']['models']['vendor/nested']
        self.assertEqual(hosted['canonical_model_id'], 'lab/nested/model')
        self.assertEqual(hosted['name'], 'Hosted Model')
        self.assertEqual(hosted['limit'], {'context': 400000})
        self.assertEqual(hosted['capabilities'], {'tools': {'supported': True, 'choice': ['auto']}})
        self.assertEqual(hosted['cost'], {'text': {'input': {'price': '0.123456789012345678'}, 'output': {'price': 0}}})
        self.assertEqual(hosted['schema'], 2)
        self.assertNotIn('license', hosted)
        self.assertNotIn('benchmarks', hosted)
        self.assertNotIn('cost', catalog['host']['models']['missing'])
        self.assertEqual(catalog['host']['models']['missing']['canonical_model_id'], 'missing')
        self.assertEqual(provenance, {
            'sourceType': 'repository',
            'sourceUrl': 'https://github.com/anomalyco/models.dev/tree/' + REVISION,
            'sourceRevision': REVISION,
        })
        json.dumps(catalog, allow_nan=False)

    def test_omit_cleans_empty_tables_after_provider_overrides(self):
        material = files()
        material['providers/host/models/vendor/nested.toml'] = '''schema = 2
base_model = "lab/nested/model"
base_model_omit = ["limit.context", "limit.output", "cost.text.input.price"]
[cost.text.input]
price = 9
'''
        catalog, _ = repository.catalog_from_archive(archive(material))
        model = catalog['host']['models']['vendor/nested']
        self.assertNotIn('limit', model)
        self.assertNotIn('cost', model)

    def test_v1_alias_is_not_an_independent_billing_provider(self):
        material = files()
        material['providers/legacy/provider.toml'] = 'name="Legacy"\n[v1_alias]\nprovider="host"\n'
        catalog, _ = repository.catalog_from_archive(archive(material))
        self.assertEqual(set(catalog), {'host'})

    def test_authored_symlink_alias_uses_explicit_target_data_but_retains_billing_identity(self):
        material = files()
        material['providers/second/provider.toml'] = 'name="Second Host"\n'
        material['providers/second/models/source.toml'] = 'schema=2\nname="Shared"\n[cost.text.input]\nprice=7.25\n'
        alias = tarfile.TarInfo('models.dev-fixture/providers/host/models/shared.toml')
        alias.type = tarfile.SYMTYPE
        alias.linkname = '../../second/models/source.toml'
        catalog, _ = repository.catalog_from_archive(archive(material, extra=[(alias, b'')]))
        shared = catalog['host']['models']['shared']
        self.assertEqual(shared['id'], 'shared')
        self.assertEqual(shared['canonical_model_id'], 'shared')
        self.assertEqual(shared['cost']['text']['input']['price'], '7.25')
        with TemporaryDirectory() as directory:
            write_files(directory, material)
            (Path(directory) / 'providers/host/models/shared.toml').symlink_to('../../second/models/source.toml')
            local, _ = repository.catalog_from_directory(directory)
            self.assertEqual(local, catalog)

    def test_model_symlink_target_escape_missing_nonmodel_and_cycles_reject(self):
        for target in ('/outside.toml', '../../../../outside.toml', '../../host/provider.toml', 'absent.toml', 'shared.toml'):
            alias = tarfile.TarInfo('models.dev-fixture/providers/host/models/shared.toml')
            alias.type = tarfile.SYMTYPE
            alias.linkname = target
            with self.subTest(target=target), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(archive(extra=[(alias, b'')]))
            with TemporaryDirectory() as directory:
                write_files(directory)
                (Path(directory) / 'providers/host/models/shared.toml').symlink_to(target)
                with self.assertRaises(repository.RepositoryCatalogError):
                    repository.catalog_from_directory(directory)

    def test_alias_depth_and_logical_expansion_are_bounded(self):
        alias = tarfile.TarInfo('models.dev-fixture/providers/host/models/shared.toml')
        alias.type = tarfile.SYMTYPE
        alias.linkname = 'missing.toml'
        with patch.object(repository, 'MAX_ALIAS_DEPTH', 1), self.assertRaises(repository.RepositoryCatalogError):
            repository.catalog_from_archive(archive(extra=[(alias, b'')]))
        material = files()
        material['providers/host/models/missing.toml'] += '# ' + 'a' * 10000 + '\n'
        aliases = []
        for index in range(10):
            link = tarfile.TarInfo(f'models.dev-fixture/providers/host/models/shared-{index}.toml')
            link.type = tarfile.SYMTYPE
            link.linkname = 'missing.toml'
            aliases.append((link, b''))
        with patch.object(repository, 'MAX_EXPANDED_BYTES', 40000), self.assertRaises(repository.RepositoryCatalogError):
            repository.catalog_from_archive(archive(material, extra=aliases))

    def test_archive_revision_requires_verified_pax_comment_and_matches_requested_commit(self):
        for comment, expected in ((None, None), ('dev', None), (REVISION, 'f' * 40), (REVISION, 'dev')):
            with self.subTest(comment=comment, expected=expected), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(archive(revision=comment), expected_revision=expected)

    def test_unsafe_paths_links_special_members_and_duplicate_paths_reject_atomically(self):
        entries = []
        for path in ('/absolute.toml', 'models.dev-fixture/../outside', 'models.dev-fixture/back\\slash',
                     'models.dev-fixture/providers/host/./provider.toml'):
            item = tarfile.TarInfo(path)
            entries.append(item)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            item = tarfile.TarInfo('models.dev-fixture/unsafe')
            item.type = kind
            item.linkname = 'providers/host/provider.toml'
            entries.append(item)
        for item in entries:
            with self.subTest(name=item.name, kind=item.type), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(archive(extra=[(item, b'')]))

    def test_compressed_expanded_member_and_toml_limits_are_enforced(self):
        body = archive()
        for key, maximum in (('MAX_ARCHIVE_BYTES', len(body) - 1), ('MAX_EXPANDED_BYTES', 1024),
                             ('MAX_MEMBERS', 2), ('MAX_TOML_BYTES', 16)):
            with self.subTest(bound=key), patch.object(repository, key, maximum), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(body)

    def test_invalid_toml_schema_missing_base_unsafe_reference_and_cycles_reject(self):
        for model in ('schema = 1\n', 'schema = 2\nbase_model = "lab/missing"\n',
                      'schema = 2\nbase_model = "../providers/host/model"\n', 'schema = [\n',
                      'schema = 2\nbase_model = 1\n', 'schema = 2\nbase_model_omit = "cost"\n',
                      'schema = 2\n[cost.text.input]\nprice=nan\n'):
            material = files()
            material['providers/host/models/vendor/nested.toml'] = model
            with self.subTest(model=model), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(archive(material))
        material = files()
        material['models/lab/nested/model.toml'] = 'schema = 2\nbase_model = "lab/second"\n'
        material['models/lab/second.toml'] = 'schema = 2\nbase_model = "lab/nested/model"\n'
        with self.assertRaises(repository.RepositoryCatalogError):
            repository.catalog_from_archive(archive(material))

    def test_bases_cannot_supply_billing_prices_or_reference_provider_models(self):
        material = files()
        material['models/lab/nested/model.toml'] += '\n[cost.text.input]\nprice=9\n'
        with self.assertRaises(repository.RepositoryCatalogError):
            repository.catalog_from_archive(archive(material))
        material = files()
        material['providers/host/models/vendor/nested.toml'] = 'schema=2\nbase_model="host/missing"\n'
        with self.assertRaises(repository.RepositoryCatalogError):
            repository.catalog_from_archive(archive(material))

    def test_repository_requires_base_files_and_each_model_owner_metadata(self):
        for removed in ('models/lab/nested/model.toml', 'providers/host/provider.toml'):
            material = files()
            del material[removed]
            with self.subTest(removed=removed), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(archive(material))

    def test_local_content_digest_is_portable_changes_with_data_and_never_claims_git_head(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            write_files(first)
            write_files(second)
            git = Path(first) / '.git'
            git.mkdir()
            (git / 'HEAD').write_text(REVISION)
            one, provenance = repository.catalog_from_directory(first)
            two, other = repository.catalog_from_directory(second)
            self.assertEqual(one, two)
            self.assertEqual(provenance, other)
            self.assertEqual(provenance['sourceType'], 'local_repository')
            self.assertIsNone(provenance['sourceUrl'])
            self.assertRegex(provenance['sourceRevision'], r'^sha256:[0-9a-f]{64}$')
            target = Path(second) / 'providers/host/models/missing.toml'
            target.write_text('schema=2\nname="Changed"\n')
            self.assertNotEqual(provenance['sourceRevision'], repository.catalog_from_directory(second)[1]['sourceRevision'])

    def test_local_digest_captures_alias_target_changes_even_for_identical_documents(self):
        with TemporaryDirectory() as directory:
            material = files()
            material['providers/host/models/identical.toml'] = material['providers/host/models/missing.toml']
            write_files(directory, material)
            alias = Path(directory) / 'providers/host/models/shared.toml'
            alias.symlink_to('missing.toml')
            _, first = repository.catalog_from_directory(directory)
            alias.unlink()
            alias.symlink_to('identical.toml')
            _, second = repository.catalog_from_directory(directory)
            self.assertNotEqual(first['sourceRevision'], second['sourceRevision'])

    def test_local_links_and_bounded_reads_reject_without_partial_catalog(self):
        with TemporaryDirectory() as directory:
            write_files(directory)
            linked = Path(directory) / 'providers/host/models/linked.toml'
            linked.symlink_to(Path(directory) / 'providers/host/models/missing.toml')
            with self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_directory(directory)
            linked.unlink()
            for key, maximum in (('MAX_MEMBERS', 2), ('MAX_TOML_BYTES', 16), ('MAX_EXPANDED_BYTES', 64)):
                with self.subTest(bound=key), patch.object(repository, key, maximum), self.assertRaises(repository.RepositoryCatalogError):
                    repository.catalog_from_directory(directory)

    def test_local_current_directory_and_large_decimal_exponents_are_bounded(self):
        material = files()
        material['providers/host/models/missing.toml'] = 'schema=2\n[cost.text.input]\nprice=1e999999999\n'
        with TemporaryDirectory() as directory:
            write_files(directory, material)
            with chdir(directory):
                catalog, _ = repository.catalog_from_directory('.')
            self.assertEqual(catalog['host']['models']['missing']['cost']['text']['input']['price'], '1E+999999999')

    def test_invalid_archive_never_raises_raw_parser_errors(self):
        for body in (b'', b'not a gzip tar archive', archive()[:-20]):
            with self.subTest(size=len(body)), self.assertRaises(repository.RepositoryCatalogError):
                repository.catalog_from_archive(body)
