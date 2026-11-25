"""Deployment contracts use mocks; these tests never start Docker."""
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

import docker_platform as native


class NativeDockerTests(unittest.TestCase):
    def test_deployment_wrapper_retains_known_limitation_top_level_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def shared_main(*args,**kwargs):
                (root/'summary.json').write_text(json.dumps({'status':'passed','native_suite':{'status':'passed_with_known_limitations','verified_cases':42,'known_limitation_cases':7}}))
                return 0
            with patch.object(native.framework_platform(),'main',side_effect=shared_main):
                self.assertEqual(native.main(['--repo','/repo','--out',str(root),'--runtimes','/private/runtimes.json']),0)
            report=json.loads((root/'summary.json').read_text())
            self.assertEqual(report['status'],'passed_with_known_limitations'); self.assertFalse(report['verified'])

    def test_full_matrix_preserves_known_limitation_counts_and_requires_real_platform_pass(self):
        from source_acceptance import assess_source
        from test_dsh_acceptance import host_loss
        cases=[{'case':a+'-'+s,'version':native.VERSIONS[a],'verified':True,'source_verified':True,'platform_verified':True} for a,s in native.DEFAULT_CASES]
        report=host_loss(); assessment=assess_source(report)
        row={**assessment,'case':report['case'],'version':report['version'],'verified':False,'source_verified':False,'platform_verified':True}
        cases=[row if case['case']==row['case'] else case for case in cases]
        summary={'status':'passed_with_known_limitations','mode':'fake','scope':'full','expected_cases':49,'executed_cases':49,'cases':cases,
                 'verified_cases':48,'known_limitation_cases':1,'failed_cases':0}
        self.assertTrue(native.suite_passed(summary))
        row['platform_verified']=False
        self.assertFalse(native.suite_passed(summary))

    def test_deployment_is_shared_without_replacing_framework_acceptance(self):
        shared=native.framework_platform()
        parameters=inspect.signature(shared.main).parameters
        self.assertIn('acceptance',parameters)
        self.assertEqual(parameters['expected_cases'].default,110)
        self.assertEqual(parameters['acceptance'].default,None)

    def test_full_matrix_needs_all_49_unique_source_and_platform_cases(self):
        rows=[{'case':a+'-'+s,'version':native.VERSIONS[a], 'verified':True,
               'source_verified':True,'platform_verified':True} for a,s in native.DEFAULT_CASES]
        summary={'status':'passed','mode':'fake','scope':'full','expected_cases':49,'executed_cases':49,'cases':rows}
        self.assertTrue(native.suite_passed(summary))
        for bad in ({**summary,'mode':'nvidia'}, {**summary,'cases':rows[:-1]},
                    {**summary,'cases':rows[:-1]+[rows[0]]},
                    {**summary,'cases':[{**rows[0],'platform_verified':False}]+rows[1:]},
                    {**summary,'cases':[{**rows[0],'version':'latest'}]+rows[1:]}):
            self.assertFalse(native.suite_passed(bad))
        self.assertFalse(native.suite_passed({**summary,'cases':[{**rows[0],'version':''}]+rows[1:]}))

    def test_host_suite_uses_only_fake_models_and_private_target_file(self):
        command=native.suite_command(Path('/repo'),Path('/outside/cases'),Path('/private/runtimes.json'),Path('/private/targets.json'))
        self.assertIn('--targets',command)
        self.assertIn('--mode',command)
        self.assertEqual(command[command.index('--mode')+1],'fake')
        self.assertNotIn('NVIDIA_API_KEY',json.dumps(command))
        self.assertNotIn('--api-key-env',command)

    def test_private_targets_do_not_enter_artifacts_or_command_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); out=root/'artifacts'; out.mkdir(); runtime=root/'private'; runtime.mkdir()
            targets={'pi':{'space_key':'synthetic-secret'}}
            smoke=native.framework_platform().Smoke(out,{'PATH':'/tools'},{'synthetic-secret'})
            cases=[{'case':a+'-'+s,'version':native.VERSIONS[a],'verified':True,'source_verified':True,'platform_verified':True}
                   for a,s in native.DEFAULT_CASES]
            def run(label,command,**kwargs):
                path=Path(command[command.index('--targets')+1])
                self.assertEqual(json.loads(path.read_text()),targets)
                self.assertEqual(path.parent,runtime)
                if os.name=='nt': self.assertTrue(os.access(path,os.R_OK|os.W_OK))
                else: self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertNotIn('synthetic-secret',json.dumps(command))
                destination=Path(command[command.index('--out')+1]); destination.mkdir()
                (destination/'summary.json').write_text(json.dumps({'status':'passed','mode':'fake','scope':'full',
                    'expected_cases':49,'executed_cases':49,'cases':cases}))
                return subprocess.CompletedProcess(command,0,'','')
            with patch.object(smoke,'run',side_effect=run):
                result=native.run_acceptance(repo=root,out=out,runtime=runtime,smoke=smoke,
                    targets=targets,runtimes=root/'runtimes.json')
            self.assertEqual(result['native_suite']['cases'],49)
            self.assertFalse((runtime/'native-targets.json').exists())
            self.assertNotIn('synthetic-secret',''.join(p.read_text() for p in out.rglob('*.json')))

    def test_controlled_metric_query_sums_delta_intervals_across_process_resets(self):
        definition=native.metric_query_definition()
        self.assertEqual(definition['metric'],'agent.api_request.count')
        self.assertEqual(definition['aggregation'],'sum')
        self.assertEqual(definition['time_aggregation'],'sum')
        self.assertEqual(definition['reduce_to'],'sum')
        self.assertEqual(definition['step'],1)
        self.assertEqual(definition['value_type'],'number')
        self.assertEqual(definition['filters'],{})

    def test_install_scripts_never_print_datasource_credentials(self):
        script=native.metrics_setup_script({'pi':{'agent_uuid':'agent-id','agent_product':'Pi'}})
        self.assertNotIn('print(',script)
        self.assertIn("datasource_type='metrics'",script)
        self.assertIn("source_system='signoz'",script)
        self.assertNotIn('raw_sql',script)

    def test_native_entrypoint_selects_shared_deployment_and_exact_matrix(self):
        with patch.object(native.framework_platform(),'main',return_value=0) as main:
            code=native.main(['--repo','/repo','--out','/outside/artifacts','--runtimes','/private/runtimes.json'])
        self.assertEqual(code,0)
        self.assertEqual(main.call_args.kwargs['expected_cases'],49)
        self.assertTrue(callable(main.call_args.kwargs['target_factory']))
        self.assertTrue(callable(main.call_args.kwargs['acceptance']))

    def test_ci_tiers_keep_native_coverage_and_share_database_contracts(self):
        root=Path(__file__).resolve().parents[4]
        content=(root/'.github/workflows/native-agent-e2e.yml').read_text()
        document=yaml.safe_load(content)
        jobs=document['jobs']
        matrix=jobs['native-source']['strategy']['matrix']['os']
        source=re.fullmatch(r"\$\{\{ fromJSON\(inputs\.tier == 'release' && '([^']+)' \|\| '([^']+)'\) \}\}",matrix)
        self.assertIsNotNone(source)
        self.assertEqual(json.loads(source[1]),['ubuntu-24.04','macos-26','windows-2025'])
        self.assertEqual(json.loads(source[2]),['ubuntu-24.04'])
        triggers=document.get('on',document.get(True))
        intel=triggers['workflow_call']['inputs']['intel_docker']
        self.assertEqual(intel['type'],'boolean')
        self.assertIs(intel['default'],False)
        self.assertNotIn('intel_docker',triggers['workflow_dispatch']['inputs'])
        docker=jobs['docker-platform']['strategy']['matrix']['os']
        self.assertEqual(docker,
            "${{ fromJSON(inputs.intel_docker && '[\"macos-15-intel\"]' || '[\"ubuntu-24.04\"]') }}")
        self.assertNotIn('docker-database',jobs)
        self.assertNotIn('tests/framework_e2e/docker_database.py',content)
        framework=yaml.safe_load((root/'.github/workflows/framework-e2e.yml').read_text())['jobs']
        database=framework['docker-database']
        self.assertEqual(database['if'],'inputs.database')
        self.assertEqual(database['strategy']['matrix']['backend'],['oceanbase','seekdb'])
        self.assertIn('tests/framework_e2e/docker_database.py','\n'.join(step.get('run','') for step in database['steps']))
        central=yaml.safe_load((root/'.github/workflows/ci.yml').read_text())['jobs']['framework']
        self.assertEqual(central['uses'],'./.github/workflows/framework-e2e.yml')
        self.assertEqual(central['with']['database'],'${{ fromJSON(needs.scope.outputs.plan).framework_database }}')
        self.assertNotIn('secrets.NVIDIA',content)
        self.assertNotIn('--mode nvidia',content)


if __name__=='__main__': unittest.main()
