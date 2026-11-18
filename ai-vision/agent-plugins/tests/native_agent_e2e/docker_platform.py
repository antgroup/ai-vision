"""Run native fake-model cases against a fresh six-service local platform.

The shared framework deployment retains image pins, current backend mounts,
public auth, precision candidate and exact-resource cleanup. No existing
endpoint or credentials are read by this entrypoint.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

from run_suite import APPS, DEFAULT_CASES, PRODUCTS, VERSIONS

_FRAMEWORK=None
METRIC_KEY='native_e2e.api_count'


def framework_platform():
    global _FRAMEWORK
    if _FRAMEWORK is None:
        directory=Path(__file__).resolve().parent.parent/'framework_e2e'
        spec=importlib.util.spec_from_file_location('_native_shared_platform',directory/'docker_platform.py')
        module=importlib.util.module_from_spec(spec)
        sys.path.insert(0,str(directory))
        try: spec.loader.exec_module(module)
        finally: sys.path.pop(0)
        _FRAMEWORK=module
    return _FRAMEWORK


def suite_passed(summary):
    rows=summary.get('cases',[])
    expected={a+'-'+s for a,s in DEFAULT_CASES}
    from source_acceptance import bounded_row
    from verify import acceptance_status
    stats_ok = (summary.get('status') != 'passed_with_known_limitations' or (
        type(summary.get('verified_cases')) is int and summary['verified_cases'] == sum(r.get('verified') is True for r in rows)
        and type(summary.get('known_limitation_cases')) is int and summary['known_limitation_cases'] == sum(bounded_row(r) for r in rows)
        and type(summary.get('failed_cases')) is int and summary['failed_cases'] == 0))
    return (stats_ok and summary.get('status') in ('passed', 'passed_with_known_limitations') and summary.get('status') == acceptance_status(rows, expected) and summary.get('mode')=='fake' and summary.get('scope')=='full'
        and summary.get('expected_cases')==summary.get('executed_cases')==len(rows)==len(expected)
        and {r.get('case') for r in rows}==expected
        and all((r.get('verified') is True and r.get('source_verified') is True or bounded_row(r)) and r.get('platform_verified') is True
                and isinstance(r.get('version'),str)
                and r['version'].removeprefix('v').split()[:1]==[VERSIONS[r['case'].split('-',1)[0]]] for r in rows))


def suite_command(repo,out,runtimes,targets):
    return [sys.executable,str(repo/'ai-vision/agent-plugins/tests/native_agent_e2e/run_suite.py'),
            '--repo',str(repo),'--out',str(out),'--runtimes',str(runtimes),'--targets',str(targets),
            '--mode','fake','--apps',','.join(APPS)]


def metric_query_definition():
    # Native cases explicitly export delta counters. Sum each admitted interval
    # across the exact window, including a new process after session resume.
    return {'mode':'simple','metric':'agent.api_request.count','aggregation':'sum',
            'time_aggregation':'sum','reduce_to':'sum','step':1,'value_type':'number','filters':{}}


def metrics_setup_script(targets):
    identities={app:{k:target[k] for k in ('agent_uuid','agent_product')} for app,target in targets.items()}
    return """import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','aivision_backend.settings')
import django
django.setup()
from copy import deepcopy
from apps.core.models import Agent, DataSource
targets="""+repr(identities)+"""
template=DataSource.objects.filter(datasource_type='metrics',source_system='signoz',enabled=1,gmt_deleted__isnull=True).first()
if template is None: raise RuntimeError('Bootstrap did not configure a physical SigNoz metrics datasource')
for target in targets.values():
    agent=Agent.objects.get(uuid=target['agent_uuid'],agent_product=target['agent_product'])
    config=deepcopy(template.config)
    config['agent_product_filter']=target['agent_product']
    config['queries']={"""+repr(METRIC_KEY)+":"+repr(metric_query_definition())+"""}
    DataSource.objects.update_or_create(agent_type=agent.agent_type,datasource_type='metrics',source_system='signoz',
        defaults={'config':config,'enabled':1,'is_default':1,'gmt_deleted':None})
"""


def register_targets(*,origin,namespace,password,private,plan,smoke):
    shared=framework_platform()
    collector='http://127.0.0.1:'+str(plan['config']['vars']['GATEWAY_HTTP_PORT'])
    targets=shared.register_targets(origin,namespace,password,private,agent_products=PRODUCTS,
                                    collector_endpoint=collector)
    for target in targets.values(): target['metric_query_key']=METRIC_KEY
    smoke.run('native-physical-metrics-setup',['docker','exec',plan['config']['vars']['BE_CONTAINER'],
        'python','-c',metrics_setup_script(targets)])
    return targets


def run_acceptance(*,repo,out,runtime,smoke,targets,runtimes,**_):
    path=runtime/'native-targets.json'
    path.touch(mode=0o600,exist_ok=False)
    path.write_text(json.dumps(targets),encoding='utf-8')
    try:
        execution=smoke.run('native-suite',suite_command(repo,out/'cases',runtimes,path),timeout=3600,check=False)
        summary=json.loads((out/'cases/summary.json').read_text(encoding='utf-8'))
        if execution.returncode or not suite_passed(summary):
            raise AssertionError('Full native49 source/three-signal runner-local API reconciliation failed')
        return {'verified':True, 'accepted':True, 'provider_reconciled':summary['status']=='passed', 'canonical_verified':True, 'native_suite':{'status':summary['status'],'cases':len(DEFAULT_CASES),'mode':'fake','versions':VERSIONS,
            'verified_cases':summary.get('verified_cases'), 'known_limitation_cases':summary.get('known_limitation_cases'), 'failed_cases':summary.get('failed_cases')}}
    finally:
        path.unlink(missing_ok=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--runtimes',type=Path,required=True,help='Private official pinned native argv prefixes')
    args=parser.parse_args(argv)
    if args.out.resolve().is_relative_to(args.repo.resolve()):
        raise ValueError('Store native acceptance evidence outside the Git checkout')
    def acceptance(**kwargs): return run_acceptance(**kwargs,runtimes=args.runtimes.resolve())
    code = framework_platform().main(['--repo',str(args.repo),'--out',str(args.out)],
        acceptance=acceptance,target_factory=register_targets,expected_cases=len(DEFAULT_CASES))
    # The shared deployment helper uses verified as its internal ingestion gate
    # and writes passed after cleanup. Preserve the native fidelity distinction
    # in this wrapper's final artifact, without changing the shared helper.
    path=args.out/'summary.json'
    if code == 0 and path.exists():
        summary=json.loads(path.read_text(encoding='utf-8'))
        if summary.get('native_suite', {}).get('status') == 'passed_with_known_limitations':
            summary['status']='passed_with_known_limitations'
            summary['verified']=False
            path.write_text(json.dumps(summary, indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'status':summary['status'], 'native_suite':summary['native_suite']}))
    return code


if __name__=='__main__': raise SystemExit(main())
