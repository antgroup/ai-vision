"""Public/native API fixtures exercise independent readback and failure gates."""
from copy import deepcopy
from collections import Counter
import json
import unittest
from unittest.mock import patch

from platform_verify import verify_platform

TARGET = {"endpoint": "http://127.0.0.1:8000", "space_id": "space-a", "agent_uuid": "agent-a",
          "agent_product": "Pi", "space_key": "synthetic-space-key", "dashboard_key": "synthetic-jwt",
          "metric_query_key": "native.api_count", "metric_value_field": "count",
          "authenticated_user_id": "authenticated-native-user"}


def fixture(unknown=False):
    spans, logs = [], []
    for i, tid in enumerate(("a"*32, "b"*32), 1):
        root, llm, tool = str(i)*16, str(i+2)*16, str(i+4)*16
        common = {"gen_ai.conversation.id": "session-a", "user.id": "business-user",
                  "gen_ai.user.id":"business-user","langfuse.user.id":"business-user"}
        resource = {"ant.agent.type": "pi", "ant.agent.product": "Pi", "ant.agent.instance": "native-instance"}
        for sid, parent, kind, operation in ((root,"","CHAT","invoke_agent"),(llm,root,"LLM","chat"),(tool,root,"TOOL","execute_tool")):
            attrs = {**common, "gen_ai.span.kind":kind, "gen_ai.operation.name":operation,
                     "gen_ai.input.messages":json.dumps([{"role":"user","content":"read receipt"}]),
                     "gen_ai.output.messages":json.dumps([{"role":"assistant","content":"receipt: actual"}])}
            if kind == "LLM":
                attrs.update({"gen_ai.request.model":"native-e2e-chat", "gen_ai.provider.name":"fake"})
                if unknown:
                    attrs.update({"gen_ai.usage.input_tokens":0,"gen_ai.usage.output_tokens":0,
                                  "agentic.usage.input_tokens.missing":"true","agentic.usage.output_tokens.missing":"true"})
                else:
                    attrs.update({"gen_ai.usage.input_tokens":12,"gen_ai.usage.output_tokens":3,"gen_ai.usage.total_tokens":15})
            spans.append({"trace_id":tid,"span_id":sid,"parent_span_id":parent,"name":kind+str(i),
                          "attributes":attrs,"resource":resource,"status":{"code":1},
                          "start_time_unix_nano":i*10**9,"end_time_unix_nano":i*10**9+1000})
        logs.append({"trace_id":tid,"span_id":llm,"body":"api_request","time_unix_nano":i*10**9,
                     "attributes":{"event.name":"api_request","gen_ai.conversation.id":"session-a"},"resource":resource})
    metrics = [{"name":"agent.api_request.count","kind":"sum","temporality":1,
                "resource":{},"attributes":{},"point":{"asInt":2,"startTimeUnixNano":"1","timeUnixNano":"2000000000"}}]
    report = {"app":"pi","source":{"verified":True},"bounds":{"from_ms":0,"to_ms":3000},
              "result":{"session_id":"session-a"},"exports":[{"spans":spans,"logs":logs,"metrics":metrics,"admitted":True}]}
    return report


def iso(ns):
    import datetime
    sec, tail=divmod(ns,10**9)
    return datetime.datetime.fromtimestamp(sec,datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{tail:09d}Z'


def api_fixture(report, mutation=None):
    spans=report['exports'][0]['spans']
    calls=[s for s in spans if s['attributes']['gen_ai.span.kind']=='LLM']
    unknown='agentic.usage.input_tokens.missing' in calls[0]['attributes']
    total={k:None if unknown else v for k,v in [('input',24),('output',6),('total',30)]}
    def public_obs(s):
        a=s['attributes']; measured=s in calls and not unknown
        details={k:v for k,v in [('input',12),('output',3),('total',15)]} if measured else {}
        if measured and 'gen_ai.usage.cache_read.input_tokens' in a:
            details.update(input=7,output=2,cache_read_input_tokens=3,cache_creation_input_tokens=2,reasoning_tokens=1)
            if mutation=='details_cache': details['cache_creation_input_tokens']=999
        return {'id':s['span_id'],'traceId':s['trace_id'],'parentObservationId':s['parent_span_id'] or None,
                'name':s['name'],'type':{'CHAT':'AGENT','LLM':'GENERATION','TOOL':'TOOL'}[a['gen_ai.span.kind']],
                'model':a.get('gen_ai.request.model'),'input':json.loads(a['gen_ai.input.messages']),
                'output':json.loads(a['gen_ai.output.messages']),'startTime':iso(s['start_time_unix_nano']),
                'endTime':iso(s['end_time_unix_nano']),'usage':{
                    **({'input':12,'output':3,'total':15} if measured else dict.fromkeys(('input','output','total'),0)),'unit':'TOKENS'},
                'usageDetails':details}
    def public_trace(tid):
        own=[s for s in spans if s['trace_id']==tid]; root=own[0]
        return {'id':tid,'name':root['name'],'sessionId':'session-a','userId':None if mutation=='native_privacy' else 'business-user',
                'input':json.loads(root['attributes']['gen_ai.input.messages']),
                'output':json.loads(root['attributes']['gen_ai.output.messages']),
                'timestamp':iso(root['start_time_unix_nano']),'observations':[public_obs(s) for s in own]}
    trace_ids=sorted({s['trace_id'] for s in spans})
    requests=[]
    def query(target,path,**kwargs):
        requests.append((path,deepcopy(kwargs)))
        params=kwargs.get('params',{}); body=kwargs.get('body',{})
        if kwargs.get('key')=='sk-aiv-invalid-native-e2e': return 401,{'code':1}
        if '/spaces/' in path and '/spaces/space-a/' not in path: return 403,{'code':1}
        if params.get('agent_uuid') and params['agent_uuid']!='agent-a':
            return (200,{'code':0,'data':{'spans':spans}}) if mutation=='isolation' else (400,{'code':1})
        if params.get('from',0)>3000: return 404,{'code':1}
        if path.endswith('/detail'):
            tid=path.split('/')[-2]; own=deepcopy([s for s in spans if s['trace_id']==tid])
            for s in own:
                a=s['attributes']; user=a.pop('user.id')
                if mutation!='native_privacy': a.update({'gen_ai.user.id':user,'langfuse.user.id':user})
                s['resource_attributes']={**s.pop('resource'),'aivision.space.id':'space-a','aivision.agent.id':'agent-a','aivision.agent.product':'Pi',
                    'aivision.user.id':TARGET['authenticated_user_id']}
                if mutation=='agent': s['resource_attributes']['aivision.agent.id']='wrong-agent'
                if mutation=='io': a['gen_ai.output.messages']='[]'
            if mutation=='missing': own.pop()
            return 200,{'code':0,'data':{'spans':own}}
        if path.endswith('/traces/list') or path=='/api/public/traces':
            page=(body or params).get('page',1); rows=[{'trace_id':t} for t in trace_ids] if path.endswith('/list') else [public_trace(t) for t in trace_ids]
            data=rows[page-1:page]
            meta={'page':page,'limit':1,'totalItems':len(rows),'totalPages':len(rows),'complete':True}
            if mutation=='pagination': meta['totalItems']=99
            if mutation=='incomplete_flag' and path.endswith('/list'): meta.pop('complete')
            return 200,{'code':0,'data':{'data':data,'meta':meta}} if path.endswith('/list') else {'data':data,'meta':meta}
        if path.startswith('/api/public/traces/'): return 200,public_trace(path.rsplit('/',1)[-1])
        if path=='/api/public/observations':
            rows=[public_obs(s) for s in spans if s['trace_id']==params['traceId']]; page=params.get('page',1)
            data=rows[(page-1)*2:page*2]
            if mutation=='duplicate' and page==2: data=[rows[0]]
            if mutation=='unknown_zero' and unknown:
                for row in data:
                    if row['type']=='GENERATION': row['usageDetails']={'input':0,'output':0,'total':0}
            return 200,{'data':data,'meta':{'page':page,'limit':2,'totalItems':len(rows),'totalPages':2}}
        if path.endswith('/sessions/usage'):
            value=dict(total)
            if 'gen_ai.usage.cache_read.input_tokens' in calls[0]['attributes']:
                value.update(cacheReadInput=6,cacheWriteInput=4,reasoning=2)
            if mutation=='unknown_zero' and unknown: value=dict.fromkeys(value,0)
            if mutation=='optional_zero': value.update(cacheReadInput=0,cacheWriteInput=0,reasoning=0)
            return 200,{'code':0,'data':{'data':[{'id':'session-a','usage':value}]}}
        if path.endswith('/sessions/io'):
            return 200,{'code':0,'data':{'data':[{'id':'session-a','inputValue':public_trace(trace_ids[0])['input'],
                    'outputValue':public_trace(trace_ids[-1])['output'],'inputSource':{'traceId':trace_ids[0]},'outputSource':{'traceId':trace_ids[-1]}}]}}
        if path.startswith('/api/public/sessions/'):
            return 200,{'id':'session-a','projectId':'agent-a','traces':[public_trace(t) for t in trace_ids]}
        if path.endswith('/traces/metrics'):
            reported={k:{'value':v,'reportedCalls':0 if unknown else 2} for k,v in total.items()}
            if mutation=='reported_bool' and unknown:
                for value in reported.values(): value['reportedCalls']=False
            value={'traceCount':2,'spanCount':6,'generationCount':2,'toolCount':2,'usage':total,'reportedUsage':reported,
                   'usageByType':[{'type':'GENERATION','callCount':2,'usage':total,'reportedUsage':reported}],
                   'coverage':{'billableCalls':2,'knownUsageCalls':0 if unknown else 2,'knownTokens':0 if unknown else 30,'knownCostCalls':0},
                   'groups':[{'dimension':body['groupBy'],'value':'native-e2e-chat' if body['groupBy']=='model' else 'fake','callCount':2,'usage':total}]}
            if 'gen_ai.usage.cache_read.input_tokens' in calls[0]['attributes']:
                value['usage']={**total,'cacheReadInput':6,'cacheWriteInput':4,'reasoning':2}
                value['groups'][0]['usage']=deepcopy(value['usage'])
                value['usageByType'][0]['usage']=deepcopy(value['usage'])
                if mutation=='type_reasoning': value['usageByType'][0]['usage']['reasoning']=999
            if mutation=='metrics_duplicate': value['groups'].append(deepcopy(value['groups'][0]))
            if mutation=='optional_zero':
                value['usage']={**total,'cacheReadInput':0,'cacheWriteInput':0,'reasoning':0}
                value['groups'][0]['usage']=deepcopy(value['usage'])
                value['usageByType'][0]['usage']=deepcopy(value['usage'])
            return 200,{'code':0,'data':value}
        if path.endswith('/events/list'):
            page=body['page']; rows=[{'time':iso(l['time_unix_nano']),'event_name':'api_request','trace_id':l['trace_id'],
                       'span_id':l['span_id'],'session_id':'session-a','body':deepcopy(l['body'])} for l in report['exports'][0]['logs']]
            return 200,{'code':0,'data':{'data':rows[page-1:page],'meta':{'page':page,'limit':1,'totalItems':2,'totalPages':2,'complete':True}}}
        if path.endswith('/query'):
            # V1 DataQueryView dispatches only the server-registered metrics
            # data item, and its number adapter returns one scalar value.
            value={'value_type':'number','data':{'value':2}}
            if mutation=='metric_duplicate': value['data']={'rows':[{'count':2},{'count':2}]}
            if mutation=='logs_metric': value={'queryKey':'native.api_count','rows':[{'count':2}]}
            return 200,{'code':0,'data':value}
        raise AssertionError('Unexpected query '+path)
    query.requests=requests
    return query


class PlatformTests(unittest.TestCase):
    def setUp(self):
        # These existing fixtures isolate API pagination/normalization behavior.
        # Raw binding has separate real capture controls below and in integrity tests.
        self.raw_patch=patch('evidence_integrity.exports_match_raw',return_value=True)
        self.raw_patch.start(); self.addCleanup(self.raw_patch.stop)

    def test_authenticated_user_missing_wrong_or_untyped_on_any_span_is_rejected(self):
        for value in ('missing', 'foreign-user', None, True, 1):
            with self.subTest(value=value):
                report=fixture(); delegate=api_fixture(report)
                def query(target,path,delegate=delegate,value=value,**kwargs):
                    status,payload=delegate(target,path,**kwargs)
                    if path.endswith('/detail') and status==200:
                        resource=payload['data']['spans'][-1]['resource_attributes']
                        if value=='missing': resource.pop('aivision.user.id')
                        else: resource['aivision.user.id']=value
                    return status,payload
                result=verify_platform(report,TARGET,query=query,poll_seconds=0)
                self.assertFalse(result['verified'])
                self.assertFalse(result['checks']['platform.native.0.exact'])

    def test_reserved_user_residue_same_null_or_foreign_cannot_pass(self):
        for value in ('business-user', None, 'foreign-user'):
            with self.subTest(value=value):
                report=fixture(); delegate=api_fixture(report)
                def query(target,path,delegate=delegate,value=value,**kwargs):
                    status,payload=delegate(target,path,**kwargs)
                    if path.endswith('/detail') and status==200:
                        payload['data']['spans'][-1]['attributes']['user.id']=value
                    return status,payload
                self.assertFalse(verify_platform(report,TARGET,query=query,poll_seconds=0)['verified'])

    def test_residue_is_rejected_when_original_source_had_no_reserved_user(self):
        from platform_verify import _native_equal
        report=fixture(); own=report['exports'][0]['spans'][:3]
        _,payload=api_fixture(report)(TARGET,'/openapi/v1/spaces/space-a/agents/Pi/trace/'+ 'a'*32 +'/detail')
        expected=deepcopy(own); actual=payload['data']['spans']
        for span in expected: span['attributes'].pop('user.id')
        self.assertTrue(_native_equal(expected,actual,TARGET))
        actual[-1]['attributes']['user.id']=None
        self.assertFalse(_native_equal(expected,actual,TARGET))

    def test_target_authenticated_scope_is_required_before_reads_including_bounded_source(self):
        for bounded in (False,True):
            for field in ('space_id','agent_uuid','agent_product','authenticated_user_id'):
                values=('missing','', ' padded ', True, None)
                if field=='authenticated_user_id': values+=('u'*37,)
                for value in values:
                    with self.subTest(bounded=bounded,field=field,value=value):
                        report=fixture(); target=deepcopy(TARGET); query=api_fixture(report)
                        if value=='missing': target.pop(field)
                        else: target[field]=value
                        assessment={'accepted':True,'canonical_verified':True,'provider_reconciled':False,'known_limitations':['synthetic-bounded-loss']}
                        if bounded:
                            report['source']['verified']=False
                            report.update(canonical_verified=True,provider_reconciled=False,status='passed_with_known_limitations',known_limitations=assessment['known_limitations'])
                        with patch('source_acceptance.assess_source',return_value=assessment):
                            result=verify_platform(report,target,query=query,poll_seconds=0)
                        self.assertFalse(result['checks']['platform.target_configured'])
                        self.assertFalse(result['verified']); self.assertEqual(query.requests,[])

    def test_bounded_source_cannot_hide_empty_or_partial_wrong_identity(self):
        assessment={'accepted':True,'canonical_verified':True,'provider_reconciled':False,
                    'known_limitations':['synthetic-bounded-loss']}
        for mutation in ('empty','partial_missing_user','partial_foreign_user'):
            with self.subTest(mutation=mutation):
                report=fixture(); report['source']['verified']=False
                report.update(canonical_verified=True,provider_reconciled=False,
                    status='passed_with_known_limitations',known_limitations=assessment['known_limitations'])
                original=json.dumps(report,sort_keys=True); delegate=api_fixture(report)
                def query(target,path,delegate=delegate,mutation=mutation,**kwargs):
                    status,payload=delegate(target,path,**kwargs)
                    if path.endswith('/detail') and status==200:
                        spans=payload['data']['spans']
                        if mutation=='empty': spans.clear()
                        else:
                            spans.pop()
                            resource=spans[0]['resource_attributes']
                            if mutation=='partial_missing_user': resource.pop('aivision.user.id')
                            else: resource['aivision.user.id']='foreign-user'
                    return status,payload
                with patch('source_acceptance.assess_source',return_value=assessment):
                    result=verify_platform(report,TARGET,query=query,poll_seconds=0)
                self.assertTrue(result['checks']['platform.source_accepted'])
                self.assertFalse(result['checks']['platform.native.0.exact'])
                self.assertFalse(result['verified'])
                self.assertEqual(json.dumps(report,sort_keys=True),original)

    def test_four_trusted_resource_fields_require_exact_native_strings(self):
        from platform_verify import _native_equal
        report=fixture(); own=report['exports'][0]['spans'][:3]
        _,payload=api_fixture(report)(TARGET,'/openapi/v1/spaces/space-a/agents/Pi/trace/'+ 'a'*32 +'/detail')
        actual=payload['data']['spans']
        for field,target_key in (('space.id','space_id'),('agent.id','agent_uuid'),('agent.product','agent_product'),('user.id','authenticated_user_id')):
            for value in ('missing',1,True,'foreign'):
                with self.subTest(field=field,value=value):
                    changed=deepcopy(actual)
                    if value=='missing': changed[-1]['resource_attributes'].pop('aivision.'+field)
                    else: changed[-1]['resource_attributes']['aivision.'+field]=value
                    self.assertFalse(_native_equal(own,changed,TARGET))
            target={**TARGET,target_key:'1'}; changed=deepcopy(actual)
            for span in changed: span['resource_attributes']['aivision.'+field]=1
            self.assertFalse(_native_equal(own,changed,target))

    def test_authenticated_user_model_string_length_without_uuid4_assumption(self):
        from platform_verify import _native_equal
        report=fixture(); own=report['exports'][0]['spans'][:3]
        _,payload=api_fixture(report)(TARGET,'/openapi/v1/spaces/space-a/agents/Pi/trace/'+ 'a'*32 +'/detail')
        for user in ('u'*32,'u'*36,'u'*37):
            with self.subTest(user=user):
                actual=deepcopy(payload['data']['spans'])
                for span in actual: span['resource_attributes']['aivision.user.id']=user
                self.assertEqual(_native_equal(own,actual,{**TARGET,'authenticated_user_id':user}),len(user)<=36)

    def test_bad_scope_on_partial_detail_is_not_retryable_missing_span(self):
        from platform_verify import _native_detail_complete
        report=fixture(); own=report['exports'][0]['spans'][:3]
        _,payload=api_fixture(report)(TARGET,'/openapi/v1/spaces/space-a/agents/Pi/trace/'+ 'a'*32 +'/detail')
        payload['data']['spans'].pop()
        payload['data']['spans'][0]['resource_attributes']['aivision.user.id']='foreign-user'
        self.assertEqual(_native_detail_complete(own,200,payload,TARGET),(False,False))

    def test_public_list_is_read_after_complete_native_details(self):
        report = fixture()
        delegate = api_fixture(report)
        ready = set()
        def query(target, path, **kwargs):
            response = delegate(target, path, **kwargs)
            if path.endswith('/detail'):
                ready.add(path.split('/')[-2])
            if path == '/api/public/traces':
                self.assertEqual(ready, {'a' * 32, 'b' * 32})
            return response
        result = verify_platform(report, TARGET, query=query)
        self.assertTrue(result['verified'], result['failures'])
        self.assertEqual(result['pagination_reads']['v1_traces']['ids'], ['a' * 32, 'b' * 32])

    def test_even_verified_normal_source_cannot_bypass_original_payload_integrity(self):
        from evidence_integrity import _decode_saved
        from test_dsh_acceptance import host_loss
        report=host_loss(); report['source']={'verified':True}; report['bounds']={'from_ms':0,'to_ms':1}
        report['exports'][0]['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['name']='fabricated'
        def exact(exports):
            return all(all(export[key]==_decode_saved(export)[key] for key in ('spans','logs','metrics')) for export in exports)
        with patch('evidence_integrity.exports_match_raw',side_effect=exact), patch('platform_verify._query_transport') as query:
            result=verify_platform(report,TARGET)
            self.assertFalse(result['checks']['platform.raw_integrity']); query.assert_not_called()

    def test_known_host_loss_source_gate_recomputes_canonical_facts(self):
        from source_acceptance import assess_source
        from test_dsh_acceptance import host_loss
        report=host_loss(); report.update(assess_source(report)); report['verified']=False
        report['bounds']={'from_ms':0,'to_ms':1}
        target={**TARGET,'agent_product':'DeepSeekHarness'}
        query=lambda *args,**kwargs:(400,{'code':1})
        result=verify_platform(report,target,query=query)
        self.assertTrue(result['checks']['platform.source_accepted'])
        self.assertFalse(result['verified'])
        for mutation in ('canonical_flag','usage','version','limitation'):
            changed=deepcopy(report)
            if mutation=='canonical_flag': changed['canonical_verified']=False
            elif mutation=='usage': changed['exports'][0]['spans'][1]['attributes']['gen_ai.usage.input_tokens']=99
            elif mutation=='version': changed['result']['version']='0.1.0-rc.8'
            else: changed['known_limitations']=[]
            with self.subTest(mutation=mutation):
                result=verify_platform(changed,target,query=query)
                self.assertFalse(result['checks']['platform.source_accepted'])

    def detail_convergence_case(self, mutation):
        report = fixture()
        delegate = api_fixture(report)
        reads = []
        clock = [0.0]
        def query(target, path, **kwargs):
            status, payload = delegate(target, path, **kwargs)
            if (path == '/openapi/v1/spaces/space-a/agents/Pi/trace/' + 'a' * 32 + '/detail'
                and kwargs.get('key') is None and kwargs.get('params', {}).get('agent_uuid') == 'agent-a'
                and kwargs.get('params', {}).get('from') == 0):
                reads.append(path)
                if len(reads) == 1 or mutation == 'never_complete':
                    rows = payload['data']['spans']
                    if mutation in ('subset', 'never_complete', 'bad_subset'):
                        rows.pop()
                    elif mutation == 'empty':
                        rows.clear()
                    elif mutation == 'http_error':
                        status = 500
                    elif mutation == 'code_error':
                        payload['code'] = 42
                    elif mutation == 'code_bool':
                        payload['code'] = False
                    elif mutation == 'malformed':
                        payload['data'].pop('spans')
                    elif mutation == 'malformed_spans':
                        payload['data']['spans'] = 'not-a-list'
                    elif mutation == 'malformed_span':
                        rows[0] = 'not-a-span'
                    elif mutation == 'malformed_attributes':
                        rows[0]['attributes'] = []
                    elif mutation == 'duplicate':
                        rows.append(deepcopy(rows[0]))
                    elif mutation == 'foreign':
                        rows[0]['span_id'] = 'c' * 16
                    elif mutation == 'parent':
                        rows[1]['parent_span_id'] = 'c' * 16
                    elif mutation == 'status':
                        rows[0]['status']['code'] = 2
                    elif mutation == 'time':
                        rows[0]['start_time_unix_nano'] += 1
                    elif mutation == 'usage':
                        rows[1]['attributes']['gen_ai.usage.input_tokens'] = 999
                    if mutation in ('io', 'bad_subset'):
                        rows[0]['attributes']['gen_ai.input.messages'] = 'WRONG_NATIVE_INPUT'
            return status, payload
        with patch('platform_verify.time.monotonic', side_effect=lambda: clock[0]), \
             patch('platform_verify.time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = verify_platform(report, TARGET, query=query, poll_seconds=2)
        return result, len(reads), clock[0]

    def test_native_detail_retries_only_exact_missing_span_subsets(self):
        for mutation in ('subset', 'empty'):
            with self.subTest(mutation=mutation):
                result, reads, elapsed = self.detail_convergence_case(mutation)
                self.assertTrue(result['verified'], result['failures'])
                self.assertEqual(reads, 2)
                self.assertEqual(elapsed, 1)
                proof = result['native_detail_reads']['a' * 32]
                self.assertEqual(len(proof), 2)
                self.assertTrue(proof[0]['exact_missing_subset'])
                self.assertFalse(proof[0]['exact'])
                self.assertTrue(proof[1]['exact'])

    def test_native_detail_content_and_transport_errors_are_never_masked(self):
        for mutation in ('io', 'usage', 'parent', 'status', 'time', 'http_error',
                         'code_error', 'code_bool', 'malformed', 'malformed_spans',
                         'malformed_span', 'malformed_attributes', 'duplicate', 'foreign', 'bad_subset'):
            with self.subTest(mutation=mutation):
                result, reads, elapsed = self.detail_convergence_case(mutation)
                self.assertFalse(result['verified'])
                self.assertIn('platform.native.0.exact', result['failures'])
                self.assertEqual(reads, 1)
                self.assertEqual(elapsed, 0)
                proof = result['native_detail_reads']['a' * 32]
                self.assertEqual(len(proof), 1)
                self.assertFalse(proof[0]['exact_missing_subset'])
                self.assertNotIn('WRONG_NATIVE_INPUT', json.dumps(result))
                self.assertNotIn('synthetic-space-key', json.dumps(result))

    def test_null_physical_metric_data_fails_without_discarding_trace_evidence(self):
        for status, code in ((200, 0), (400, 3002)):
            with self.subTest(status=status, code=code):
                report = fixture()
                base = api_fixture(report)
                def query(target, path, **kwargs):
                    if path.endswith('/query'):
                        return status, {'code': code, 'data': None}
                    return base(target, path, **kwargs)
                result = verify_platform(report, TARGET, query=query, poll_seconds=0)
                self.assertFalse(result['verified'])
                self.assertIn('platform.otel_metrics.physical_count', result['failures'])
                self.assertTrue(result['checks']['platform.native.0.exact'])
                self.assertTrue(result['native_detail_reads'])

    def test_native_error_evidence_records_only_known_categories(self):
        for message, expected in (
            ('Incomplete trace spans or storage row limit; narrow the time range', 'incomplete_trace'),
            ('Invalid SigNoz response', 'invalid_response'),
            ('Trace changed during detail read; retry', 'changed_trace'),
            ('Invalid or shrinking trace count during detail read', 'invalid_count'),
            ('Telemetry record not found', 'not_found'),
            ('synthetic-space-key private upstream error', None),
        ):
            report = fixture()
            base = api_fixture(report)
            def query(target, path, **kwargs):
                if path.endswith('/detail') and '/spaces/space-a/' in path:
                    return 400, {'code': 3035, 'message': message}
                return base(target, path, **kwargs)
            result = verify_platform(report, TARGET, query=query, poll_seconds=0)
            self.assertFalse(result['verified'])
            proof = result['native_detail_reads']['a' * 32]
            self.assertEqual(len(proof), 1)
            self.assertEqual(proof[0]['error_kind'], expected)
            self.assertNotIn('synthetic-space-key', json.dumps(result))

    def test_native_detail_missing_span_wait_has_a_fixed_deadline(self):
        result, reads, elapsed = self.detail_convergence_case('never_complete')
        self.assertFalse(result['verified'])
        self.assertEqual(reads, 3)
        self.assertEqual(elapsed, 2)

    def convergence_case(self, mutation):
        report = fixture()
        delegate = api_fixture(report)
        reads = []
        clock = [0.0]
        def query(target, path, **kwargs):
            if path.endswith('/traces/list'):
                page = kwargs['body']['page']
                reads.append(page)
                if mutation == 'unauthorized' and len(reads) == 1:
                    return 401, {'code': 1}
                if mutation == 'http_error' and len(reads) == 1:
                    return 500, {'code': 1}
                if mutation in ('subset', 'empty') and len(reads) == 1:
                    rows = [] if mutation == 'empty' else [{'trace_id': 'a' * 32}]
                    return 200, {'code': 0, 'data': {'data': rows, 'meta': {
                        'page': 1, 'limit': 1, 'totalItems': len(rows),
                        'totalPages': len(rows), 'complete': True}}}
                if mutation == 'never_complete':
                    return 200, {'code': 0, 'data': {'data': [{'trace_id': 'a' * 32}], 'meta': {
                        'page': 1, 'limit': 1, 'totalItems': 1, 'totalPages': 1, 'complete': True}}}
                status, payload = delegate(target, path, **kwargs)
                if mutation == 'duplicate' and len(reads) <= 2:
                    payload['data']['data'][0]['trace_id'] = 'a' * 32
                if mutation == 'foreign_id' and len(reads) == 1:
                    payload['data']['data'][0]['trace_id'] = 'c' * 32
                if mutation == 'bad_metadata' and len(reads) == 1:
                    payload['data']['meta']['totalItems'] = 99
                if mutation == 'incomplete' and len(reads) == 1:
                    payload['data']['meta']['complete'] = False
                return status, payload
            return delegate(target, path, **kwargs)
        def sleep(seconds):
            clock[0] += seconds
        with patch('platform_verify.time.monotonic', side_effect=lambda: clock[0]), \
             patch('platform_verify.time.sleep', side_effect=sleep):
            result = verify_platform(report, TARGET, query=query, poll_seconds=2)
        return result, reads, clock[0]

    def test_native_pagination_waits_only_for_a_valid_expected_subset(self):
        for mutation in ('subset', 'empty'):
            with self.subTest(mutation=mutation):
                result, reads, elapsed = self.convergence_case(mutation)
                self.assertTrue(result['verified'], result['failures'])
                self.assertEqual(reads, [1, 1, 2])
                self.assertEqual(elapsed, 1)
                proof = result['pagination_reads']['native_traces']
                self.assertEqual(len(proof), 2)
                self.assertFalse(proof[0]['inventory_matches'])
                self.assertTrue(proof[1]['inventory_matches'])
                self.assertEqual(proof[1]['pages'][0]['status'], 200)
                self.assertEqual(proof[1]['pages'][0]['code'], 0)
                self.assertNotIn('synthetic-space-key', json.dumps(proof))
                self.assertNotIn('read receipt', json.dumps(proof))

    def test_native_pagination_errors_cannot_be_masked_by_later_valid_pages(self):
        for mutation, expected_reads in (
            ('unauthorized', [1]), ('http_error', [1]), ('duplicate', [1, 2]),
            ('foreign_id', [1, 2]), ('bad_metadata', [1]), ('incomplete', [1]),
        ):
            with self.subTest(mutation=mutation):
                result, reads, elapsed = self.convergence_case(mutation)
                self.assertFalse(result['verified'])
                self.assertIn('platform.native.pagination', result['failures'])
                self.assertEqual(reads, expected_reads)
                self.assertEqual(elapsed, 0)

    def test_native_pagination_subset_polling_has_a_fixed_deadline(self):
        result, reads, elapsed = self.convergence_case('never_complete')
        self.assertFalse(result['verified'])
        self.assertEqual(reads, [1, 1, 1])
        self.assertEqual(elapsed, 2)

    def test_pagination_proof_preserves_only_safe_status_ids_and_metadata(self):
        report = fixture()
        delegate = api_fixture(report)
        secret = 'private-synthetic-payload-do-not-record'
        def query(target, path, **kwargs):
            if path.endswith('/traces/list'):
                return 200, {'code': 0, 'headers': {'Authorization': secret}, 'message': secret,
                    'data': {'data': [{'trace_id': secret, 'input': secret, 'output': secret}],
                             'meta': {'page': 1, 'limit': 1, 'totalItems': secret,
                                      'totalPages': 1, 'complete': True, 'credential': secret}}}
            return delegate(target, path, **kwargs)
        result = verify_platform(report, TARGET, query=query, poll_seconds=0)
        self.assertFalse(result['checks']['platform.native.pagination'])
        self.assertNotIn(secret, json.dumps(result))
        page = result['pagination_reads']['native_traces'][0]['pages'][0]
        self.assertEqual(page['ids'], [{'invalid_type': 'str'}])
        self.assertEqual(page['meta']['totalItems'], {'invalid_type': 'str'})
        self.assertNotIn('headers', page)

    def check_case(self,mutation=None,unknown=False,target=None):
        report=fixture(unknown); query=api_fixture(report,mutation)
        return verify_platform(report,target or TARGET,query=query,poll_seconds=0),query
    def test_complete_actual_schema_and_all_signal_reads(self):
        result,query=self.check_case()
        self.assertTrue(result['verified'],result['failures'])
        self.assertTrue(any(p.endswith('/events/list') for p,_ in query.requests))
        self.assertTrue(any(p.endswith('/query') for p,_ in query.requests))
        self.assertNotIn('synthetic-space-key',json.dumps(result))
        self.assertNotIn('synthetic-jwt',json.dumps(result))
    def test_metric_read_diagnostics_distinguish_auth_and_count_failure_without_retry_or_payload(self):
        for status, value in ((200, 2.0), (401, 'synthetic-jwt'), (200, 999)):
            report=fixture(); delegate=api_fixture(report); reads=[]
            def query(target, path, **kwargs):
                if path.endswith('/query'):
                    reads.append(path)
                    return status, {'code':0 if status==200 else 1,'private':'synthetic-jwt',
                        'data':{'value_type':'number','data':{'value':value}}}
                return delegate(target,path,**kwargs)
            with self.subTest(status=status,value=value):
                result=verify_platform(report,TARGET,query=query)
                self.assertEqual(len(reads),1)
                self.assertEqual(result['metric_read']['status'],status)
                self.assertEqual(result['metric_read']['expected'],2)
                self.assertEqual(result['metric_read']['value'],value if type(value) in (int,float) else {'invalid_type':'str'})
                self.assertEqual(result['verified'],status==200 and value==2)
                self.assertNotIn('synthetic-jwt',json.dumps(result))
    def test_unknown_usage_preserved(self):
        result,_=self.check_case(unknown=True)
        self.assertTrue(result['verified'],result['failures'])
    def test_negative_contracts(self):
        for mutation in ('pagination','incomplete_flag','duplicate','agent','missing','io','metrics_duplicate','metric_duplicate','logs_metric','isolation'):
            with self.subTest(mutation=mutation):
                result,_=self.check_case(mutation)
                self.assertFalse(result['verified'])
                self.assertTrue(result['failures'])
    def test_unknown_is_not_zero(self):
        result,_=self.check_case('unknown_zero',unknown=True)
        self.assertFalse(result['verified'])
    def test_reportedness_count_is_an_integer_not_boolean(self):
        result,_=self.check_case('reported_bool',unknown=True)
        self.assertFalse(result['verified'])
    def test_missing_optional_cache_and_reasoning_cannot_become_known_zero(self):
        result,_=self.check_case('optional_zero')
        self.assertFalse(result['verified'])
    def test_missing_dashboard_auth_cannot_claim_three_signal_readback(self):
        target={k:v for k,v in TARGET.items() if k!='dashboard_key'}
        result,_=self.check_case(target=target)
        self.assertFalse(result['verified'])
    def test_controlled_delta_metric_query_rejects_cumulative_source(self):
        report=fixture()
        report['exports'][0]['metrics'][0]['temporality']=2
        result=verify_platform(report,TARGET,query=api_fixture(report))
        self.assertFalse(result['verified'])
        self.assertIn('platform.otel_metrics.delta_contract',result['failures'])
    def test_invalid_metric_temporality_fails_without_interrupting_readback(self):
        report=fixture()
        report['exports'][0]['metrics'][0]['temporality']=None
        result=verify_platform(report,TARGET,query=api_fixture(report))
        self.assertFalse(result['verified'])
        self.assertIn('platform.otel_metrics.delta_contract',result['failures'])
    def test_unverified_source_does_not_issue_reads(self):
        report=fixture(); report['source']['verified']=False; query=api_fixture(report)
        result=verify_platform(report,TARGET,query=query)
        self.assertFalse(result['verified']); self.assertEqual(query.requests,[])
    def test_cache_and_reasoning_preserve_inclusive_core_and_exclusive_v1_details(self):
        report=fixture()
        for s in report['exports'][0]['spans']:
            if s['attributes']['gen_ai.span.kind']=='LLM':
                s['attributes'].update({'gen_ai.usage.cache_read.input_tokens':3,
                    'gen_ai.usage.cache_creation.input_tokens':2,'gen_ai.usage.reasoning.output_tokens':1})
        good=verify_platform(report,TARGET,query=api_fixture(report))
        self.assertTrue(good['verified'],good['failures'])
        bad=verify_platform(report,TARGET,query=api_fixture(report,'details_cache'))
        self.assertFalse(bad['verified'])
        bad=verify_platform(report,TARGET,query=api_fixture(report,'type_reasoning'))
        self.assertFalse(bad['verified'])
    def test_dashboard_cookie_is_explicit_and_not_saved(self):
        target={k:v for k,v in TARGET.items() if k!='dashboard_key'}
        target['dashboard_headers']={'Cookie':'synthetic-private-cookie'}
        result,query=self.check_case(target=target)
        self.assertTrue(result['verified'],result['failures'])
        call=next(k for p,k in query.requests if p.endswith('/query'))
        self.assertEqual(call['headers'],target['dashboard_headers'])
        self.assertEqual(call['params']['data_item'],'native.api_count')
        self.assertEqual(call['params']['agent_product'],'Pi')
        self.assertEqual(call['params']['fromTimestamp'],'1970-01-01T00:00:00.000000000Z')
        self.assertEqual(call['params']['toTimestamp'],'1970-01-01T00:00:03.000000000Z')
        self.assertNotIn('body',call)
        self.assertTrue(any(p=='/api/v1/spaces/space-a/query' for p,_ in query.requests))
        self.assertNotIn('synthetic-private-cookie',json.dumps(result))

    def test_native_username_is_preserved_without_inventing_business_user_aliases(self):
        report=fixture()
        for s in report['exports'][0]['spans']:
            s['attributes'].pop('gen_ai.user.id'); s['attributes'].pop('langfuse.user.id')
            s['attributes']['ant.username']='business-user'
        result=verify_platform(report,TARGET,query=api_fixture(report,'native_privacy'))
        self.assertTrue(result['verified'],result['failures'])
        delegate=api_fixture(report,'native_privacy')
        def wrong_alias(target,path,**kwargs):
            status,value=delegate(target,path,**kwargs)
            if path.endswith('/detail') and status==200:
                value['data']['spans'][0]['attributes']['ant.username']='wrong-user'
            return status,value
        self.assertFalse(verify_platform(report,TARGET,query=wrong_alias)['verified'])

    def test_structured_log_body_preserves_exact_json_and_event_name(self):
        report=fixture()
        for log in report['exports'][0]['logs']:
            log['body']={'event.name':'api_request','details':['actual',1]}
            log['attributes'].pop('event.name')
        result=verify_platform(report,TARGET,query=api_fixture(report))
        self.assertTrue(result['verified'],result['failures'])
        delegate=api_fixture(report)
        def wrong_body(target,path,**kwargs):
            status,value=delegate(target,path,**kwargs)
            if path.endswith('/events/list'):
                value['data']['data'][0]['body']['details'][1]=999
            return status,value
        self.assertFalse(verify_platform(report,TARGET,query=wrong_body)['verified'])

    def test_native_uid_preserves_reserved_user_identity_without_username_equivalence(self):
        report=fixture()
        for span in report['exports'][0]['spans']:
            attributes=span['attributes']
            attributes.pop('gen_ai.user.id'); attributes.pop('langfuse.user.id')
            attributes['ant.uid']='business-user'
            attributes['ant.username']='friendly-name'
        result=verify_platform(report,TARGET,query=api_fixture(report,'native_privacy'))
        self.assertTrue(result['verified'],result['failures'])
        for mutation in ('wrong','missing','other_attribute'):
            with self.subTest(mutation=mutation):
                delegate=api_fixture(report,'native_privacy')
                def changed(target,path,**kwargs):
                    status,value=delegate(target,path,**kwargs)
                    if path.endswith('/detail') and status==200:
                        attributes=value['data']['spans'][0]['attributes']
                        if mutation=='wrong': attributes['ant.uid']='wrong-user'
                        elif mutation=='missing': attributes.pop('ant.uid')
                        else: attributes['ant.username']='wrong-name'
                    return status,value
                self.assertFalse(verify_platform(report,TARGET,query=changed)['verified'])

if __name__=='__main__': unittest.main()
