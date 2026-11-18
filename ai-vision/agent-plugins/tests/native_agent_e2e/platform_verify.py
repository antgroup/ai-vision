"""Independent read-only native/API readback, never an ingestion mechanism.

Only the established framework HTTP query transport is shared. Expectations
come from original native OTLP records, without importing product normalizers
or framework business fixtures. Target credentials remain in memory.
"""
from collections import Counter, defaultdict
import datetime
import importlib.util
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid

try:
    from .verify import metric_count
except ImportError:
    from verify import metric_count

FIELDS = ('input', 'output', 'total')
IDENTITY_RESOURCE_FIELDS = {'space_id', 'space.id', 'aivision.space.id', 'ai.vision.space.id',
    'ant.space.id', 'user_id', 'user.id', 'aivision.user.id', 'agent_id', 'agent.id',
    'aivision.agent.id', 'agent_product', 'agent.product', 'aivision.agent.product', 'aivision.ingest.key_id'}


def _authenticated_target(target):
    """Require an explicit current-user scope without assuming UUID4 syntax."""
    fields = ('space_id', 'agent_uuid', 'agent_product', 'authenticated_user_id')
    return (
        isinstance(target, dict)
        and all(type(target.get(key)) is str and target[key]
                and target[key].strip() == target[key] for key in fields)
        and len(target['authenticated_user_id']) <= 36
    )


def _http_query():
    path=Path(__file__).resolve().parent.parent/'framework_e2e/verify_rag.py'
    spec=importlib.util.spec_from_file_location('_native_http_transport',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.http_query


def _query_transport(target,path,*,params=None,body=None,key=None,basic=False,headers=None):
    if headers is None:
        return _http_query()(target,path,params=params,body=body,key=key,basic=basic)
    url=str(target['endpoint']).rstrip('/')+path
    if params: url+='?'+urllib.parse.urlencode(params)
    # Dashboard browser auth is an explicit in-memory input. No cookie/key or
    # raw response is ever attached to the readback report.
    request=urllib.request.Request(url,data=None if body is None else json.dumps(body).encode(),
        headers={**headers,'Accept':'application/json',**({'Content-Type':'application/json'} if body is not None else {})},
        method='GET' if body is None else 'POST')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=20) as response:
            return response.status,json.loads(response.read())
    except urllib.error.HTTPError as response:
        try: payload=json.loads(response.read())
        except (ValueError,UnicodeDecodeError): payload={}
        return response.code,payload


def _decoded(value):
    if isinstance(value,str):
        try: return json.loads(value)
        except ValueError: pass
    return value


def _event_name(log):
    name=log.get('attributes',{}).get('event.name',log.get('event_name'))
    if name is not None: return name
    body=_decoded(log.get('body'))
    return body.get('event.name') if isinstance(body,dict) else body


def _event_body(value):
    return json.dumps(_decoded(value),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _iso(value):
    if value is None: return None
    seconds,fraction=divmod(int(value),10**9)
    return datetime.datetime.fromtimestamp(seconds,datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{fraction:09d}Z'


def _count(value):
    return value if type(value) is int and value>=0 else None


def _kind(span):
    a=span.get('attributes',{})
    operation=a.get('gen_ai.operation.name')
    kind={'chat':'GENERATION','completion':'GENERATION','embedding':'EMBEDDING',
          'embeddings':'EMBEDDING','execute_tool':'TOOL','invoke_agent':'AGENT'}.get(operation)
    return kind or {'LLM':'GENERATION','CHAT':'AGENT'}.get(str(a.get('gen_ai.span.kind','SPAN')).upper(),str(a.get('gen_ai.span.kind','SPAN')).upper())


def _usage(span):
    a=span.get('attributes',{})
    result={}
    for field in FIELDS:
        missing=a.get('agentic.usage.'+field+'_tokens.missing')
        result[field]=None if missing is True or missing=='true' else _count(a.get('gen_ai.usage.'+field+'_tokens'))
    if result['total'] is None and result['input'] is not None and result['output'] is not None:
        result['total']=result['input']+result['output']
    # A host-initialized redundant total is not a reported total when both
    # underlying measurements were explicitly marked missing.
    if result['input'] is None and result['output'] is None:
        if any(a.get('agentic.usage.'+f+'_tokens.missing') in (True,'true') for f in ('input','output')):
            result['total']=None
    return result


def _calls(spans):
    return [s for s in spans if _kind(s) in ('GENERATION','EMBEDDING')]


def _parts(span):
    a=span.get('attributes',{})
    return {'cacheReadInput':_count(a.get('gen_ai.usage.cache_read.input_tokens')),
            'cacheWriteInput':_count(a.get('gen_ai.usage.cache_creation.input_tokens')),
            'reasoning':_count(a.get('gen_ai.usage.reasoning.output_tokens'))}


def _detail_usage(span):
    values=_usage(span); parts=_parts(span)
    values['input']=values['input']-sum(parts[f] or 0 for f in ('cacheReadInput','cacheWriteInput')) if values['input'] is not None else None
    values['output']=values['output']-(parts['reasoning'] or 0) if values['output'] is not None else None
    return {**values,**{key:parts[field] for field,key in (
        ('cacheReadInput','cache_read_input_tokens'),('cacheWriteInput','cache_creation_input_tokens'),('reasoning','reasoning_tokens'))}}


def _known_parts_equal(actual,spans):
    calls=_calls(spans)
    for field in ('cacheReadInput','cacheWriteInput','reasoning'):
        values=[_parts(s)[field] for s in calls]
        if values and all(v is not None for v in values):
            if field not in actual or type(actual[field]) is not int or actual[field]!=sum(values): return False
        elif actual.get(field) is not None:
            return False
    return True


def _totals(spans):
    values=[_usage(s) for s in _calls(spans)]
    return {f:sum(v[f] for v in values) if values and all(v[f] is not None for v in values) else None for f in FIELDS}


def _reported(spans):
    values=[_usage(s) for s in _calls(spans)]
    return {f:{'value':sum(v[f] for v in values if v[f] is not None) if any(v[f] is not None for v in values) else None,
               'reportedCalls':sum(v[f] is not None for v in values)} for f in FIELDS}


def _reported_equal(actual,expected):
    return (isinstance(actual,dict) and all(isinstance(actual.get(f),dict)
        and _exact_fields(actual[f],wanted) for f,wanted in expected.items()))


def _exact_fields(actual,expected):
    return isinstance(actual,dict) and all(f in actual and actual[f]==v and (v is None or type(actual[f]) is type(v)) for f,v in expected.items())


def _io(span,direction):
    a=span.get('attributes',{})
    keys=('gen_ai.'+direction+'.messages','langfuse.observation.'+direction,direction+'.value',
          'gen_ai.tool.call.'+('arguments' if direction=='input' else 'result'),'aivision.request.'+direction)
    return next((_decoded(a[k]) for k in keys if k in a),None)


def _user(span):
    a=span.get('attributes',{})
    # user.id is a reserved collector identity, deliberately scrubbed from
    # attributes. Business attribution uses only explicit GenAI/Langfuse keys.
    return next((a[k] for k in ('gen_ai.user.id','langfuse.user.id') if a.get(k) is not None),None)


def _status(span):
    value=span.get('status',{})
    if isinstance(value,dict): value=value.get('code',0)
    if type(value) is int and value in (0,1,2): return value
    return {'0':0,'1':1,'2':2,'UNSET':0,'OK':1,'ERROR':2,'STATUS_CODE_UNSET':0,'STATUS_CODE_OK':1,'STATUS_CODE_ERROR':2}.get(value,-1)


def _native_equal(expected,actual,target):
    if not _authenticated_target(target): return False
    ids=[(s.get('trace_id'),s.get('span_id')) for s in actual]
    wanted={(s.get('trace_id'),s.get('span_id')) for s in expected}
    if not ids or len(ids)!=len(set(ids)) or set(ids)!=wanted: return False
    index=dict(zip(ids,actual))
    for s in expected:
        live=index[(s['trace_id'],s['span_id'])]
        if any(live.get(k)!=s.get(k) for k in ('trace_id','span_id','name')): return False
        if (live.get('parent_span_id') or None)!=(s.get('parent_span_id') or None): return False
        if _status(live)!=_status(s) or _kind(live)!=_kind(s) or _usage(live)!=_usage(s): return False
        payload=live.get('payload') or {}
        if any(str(payload.get(k,live.get(k)))!=str(s.get(k)) for k in ('start_time_unix_nano','end_time_unix_nano')): return False
        a=live.get('attributes',{})
        if not isinstance(a,dict) or 'user.id' in a: return False
        for k,v in s.get('attributes',{}).items():
            if k=='user.id':
                business=[key for key in ('gen_ai.user.id','langfuse.user.id') if key in s.get('attributes',{})]
                if business:
                    if any(a.get(key)!=s['attributes'][key] for key in business): return False
                else:
                    # Native Hermes uses a stable uid separately from the
                    # display username. Either original native alias must
                    # prove the scrubbed reserved identity and remain exact.
                    native_aliases=[key for key in ('ant.uid','ant.username')
                                    if s['attributes'].get(key)==v]
                    if not native_aliases or any(a.get(key)!=v for key in native_aliases):
                        return False
            elif k not in a or a[k]!=v: return False
        resource=live.get('resource_attributes',live.get('resource',{}))
        if not isinstance(resource,dict): return False
        if any(k not in IDENTITY_RESOURCE_FIELDS and (k not in resource or resource[k]!=v) for k,v in s.get('resource',{}).items()): return False
        if any(type(resource.get(k)) is not str or resource[k]!=v for k,v in (
                ('aivision.space.id',target['space_id']),('aivision.agent.id',target['agent_uuid']),
                ('aivision.agent.product',target['agent_product']),
                ('aivision.user.id',target['authenticated_user_id']))): return False
    return True


def _native_detail_complete(expected,status,payload,target):
    """Return exactness and whether only correctly reported spans are missing."""
    if (status!=200 or not isinstance(payload,dict)
        or type(payload.get('code')) is not int or payload['code']!=0):
        return False,False
    native=_data(payload)
    if not isinstance(native,dict) or not isinstance(native.get('spans'),list):
        return False,False
    actual=native['spans']
    if not all(isinstance(span,dict) for span in actual):
        return False,False
    try:
        wanted={(span['trace_id'],span['span_id']):span for span in expected}
        ids=[(span.get('trace_id'),span.get('span_id')) for span in actual]
        if len(ids)!=len(set(ids)) or not set(ids).issubset(wanted):
            return False,False
        # Even a partial detail must preserve every returned span's original
        # IO, usage, identity, parent, status and time. Complete mismatches
        # cannot become green by replacing the response on a later attempt.
        subset=[wanted[identity] for identity in ids]
        if actual and not _native_equal(subset,actual,target):
            return False,False
        complete=len(ids)==len(wanted)
        return complete,not complete
    except (AttributeError,KeyError,TypeError,ValueError):
        return False,False


def _v1_observations(expected,actual):
    ids=[r.get('id') for r in actual]
    wanted={s['span_id']:s for s in expected}
    if len(ids)!=len(set(ids)) or set(ids)!=set(wanted): return False
    for observed in actual:
        s=wanted[observed['id']]; a=s.get('attributes',{})
        expected_fields={'traceId':s['trace_id'],'name':s['name'],'parentObservationId':s.get('parent_span_id') or None,
            'type':_kind(s),'model':a.get('gen_ai.request.model',a.get('gen_ai.response.model')),
            'input':_io(s,'input'),'output':_io(s,'output'),
            'startTime':_iso(s.get('start_time_unix_nano')),'endTime':_iso(s.get('end_time_unix_nano'))}
        if any(k not in observed or observed[k]!=v for k,v in expected_fields.items()): return False
        u=_usage(s); legacy=observed.get('usage',{})
        if legacy.get('unit')!='TOKENS' or not _exact_fields(legacy,{k:v if v is not None else 0 for k,v in u.items()}): return False
        details=observed.get('usageDetails')
        details={} if details is None else details
        if not isinstance(details,dict): return False
        if any((k in details and details[k] is not None) if v is None else (k not in details or details[k]!=v or type(details[k]) is not int) for k,v in _detail_usage(s).items()): return False
    return True


def _v1_trace(expected,actual,*,observations=True):
    roots=[s for s in expected if not s.get('parent_span_id')]
    if len(roots)!=1: return False
    root=roots[0]
    fields={'id':root['trace_id'],'name':root['name'],'sessionId':root['attributes'].get('gen_ai.conversation.id'),
            'userId':_user(root),'input':_io(root,'input'),'output':_io(root,'output'),
            'timestamp':_iso(min(s['start_time_unix_nano'] for s in expected))}
    return (isinstance(actual,dict) and all(k in actual and actual[k]==v for k,v in fields.items())
            and (not observations or _v1_observations(expected,actual.get('observations',[]))))


def _data(payload):
    value=payload.get('data',{}) if isinstance(payload,dict) else {}
    return value.get('data',value) if isinstance(value,dict) and 'data' in value and not isinstance(value['data'],list) else value


def _safe_page_fact(value):
    return value if value is None or type(value) in (int, bool) else {'invalid_type':type(value).__name__}


def _safe_trace_id(value):
    return value if isinstance(value,str) and len(value)==32 and all(c in '0123456789abcdef' for c in value.lower()) else {'invalid_type':type(value).__name__}


def _safe_span_id(value):
    return value if isinstance(value,str) and len(value)==16 and all(c in '0123456789abcdef' for c in value.lower()) else {'invalid_type':type(value).__name__}


def _paged(query,target,path,*,body=None,params=None,basic=False,limit=2,page_proof=None):
    rows=[]; declared=None
    for page in range(1,101):
        values={**(body or params or {}),'page':page,'limit':limit}
        status,payload=query(target,path,**({'body':values} if body is not None else {'params':values}),basic=basic)
        if page_proof is not None:
            container=payload.get('data',{}) if isinstance(payload,dict) else None
            current=(container if isinstance(container,list) else container.get('data',[]) if isinstance(container,dict) else [])
            meta=(payload.get('meta',{}) if isinstance(container,list) else container.get('meta',{}) if isinstance(container,dict) else {})
            meta=meta if isinstance(meta,dict) else {}
            page_proof.append({'page':page,'status':_safe_page_fact(status),
                'code':_safe_page_fact(payload.get('code') if isinstance(payload,dict) else None),
                'ids':[_safe_trace_id(row.get('trace_id') if isinstance(row,dict) else None)
                       for row in current[:200]] if isinstance(current,list) else [],
                'meta':{key:_safe_page_fact(meta.get(key)) for key in ('page','limit','totalItems','totalPages','complete')}})
        if status!=200 or not isinstance(payload,dict) or payload.get('code',0)!=0: return rows,False
        container=payload.get('data',{})
        if isinstance(container,list): current=container; meta=payload.get('meta',{})
        elif isinstance(container,dict): current=container.get('data',[]); meta=container.get('meta',{})
        else: return rows,False
        total,pages=meta.get('totalItems'),meta.get('totalPages')
        if (not isinstance(current,list) or type(total) is not int or total<0 or type(pages) is not int
            or pages<0 or pages>100 or meta.get('page')!=page or meta.get('limit')!=limit
            or meta.get('complete',True) is not True
            or (body is not None and meta.get('complete') is not True)): return rows,False
        if pages!=max(1,(total+limit-1)//limit) and not (total==0 and pages==0): return rows,False
        if declared is not None and declared!=(total,pages): return rows,False
        declared=(total,pages); rows.extend(current)
        if page>=pages: return rows,len(rows)==total
        if len(current)!=limit: return rows,False
    return rows,False


def _native_trace_inventory(query,target,path,*,body,expected,deadline,proof):
    """Wait only for a complete, valid source subset; never hide page errors."""
    for attempt in range(1,62):
        pages=[]
        rows,complete=_paged(query,target,path,body=body,limit=1,page_proof=pages)
        ids=[row.get('trace_id') if isinstance(row,dict) else None for row in rows]
        valid_ids=all(isinstance(value,str) for value in ids)
        unique=valid_ids and len(ids)==len(set(ids))
        subset=unique and set(ids).issubset(expected)
        matched=complete and subset and set(ids)==expected
        entry={'attempt':attempt,'pages':pages,'complete':complete,'row_count':len(rows),
               'expected_count':len(expected),'unique':unique,'expected_subset':subset,'inventory_matches':matched}
        proof.append(entry)
        if not complete or not subset or matched:
            return rows,matched
        # The strict pagination scan succeeded and returned no foreign or
        # repeated IDs. A missing expected trace can still be in ingestion.
        remaining=deadline-time.monotonic()
        if remaining<=0:
            return rows,False
        time.sleep(min(1,remaining))
    return rows,False


def verify_platform(report,target,*,query=None,poll_seconds=0):
    """Explicit invocation performs only bounded authenticated API reads.

    SpaceKey covers native/public trace/session/events. Physical OTEL metrics
    require dashboard_key or dashboard_headers and a server-registered metrics
    data item in metric_query_key returning a V1 number scalar. No JWT/key,
    request headers, raw API response or target dictionary enters the result.
    """
    checks={}; urls=[]; pagination_reads={}; native_detail_reads={}; metric_read={}
    def check(name,condition): checks[name]=bool(condition)
    def result(): return {'verified':bool(checks) and all(checks.values()),'checks':checks,
                         'failures':[k for k,v in checks.items() if not v],'ui_urls':urls,
                         'pagination_reads':pagination_reads,'native_detail_reads':native_detail_reads,
                         'metric_read':metric_read}
    from evidence_integrity import exports_match_raw
    check('platform.raw_integrity', exports_match_raw(report.get('exports')))
    source_ok = report.get('source', {}).get('verified') is True
    if not source_ok:
        from source_acceptance import assess_source
        assessment = assess_source(report)
        source_ok = (assessment['accepted'] is True and assessment['canonical_verified'] is True
            and assessment['provider_reconciled'] is False and report.get('canonical_verified') is True
            and report.get('provider_reconciled') is False and report.get('status') == 'passed_with_known_limitations'
            and report.get('known_limitations') == assessment['known_limitations'])
    check('platform.source_accepted', source_ok)
    check('platform.target_configured',_authenticated_target(target) and all(target.get(k) for k in ('endpoint','space_id','agent_uuid','agent_product','space_key')))
    bounds=report.get('bounds',{}); window={'from':bounds.get('from_ms'),'to':bounds.get('to_ms')}
    check('platform.exact_window',all(type(v) is int for v in window.values()) and window['from']<window['to'])
    if not all(checks.values()): return result()
    query=query or _query_transport
    spans=[s for e in report.get('exports',[]) for s in e.get('spans',[])]
    logs=[s for e in report.get('exports',[]) for s in e.get('logs',[])]
    metric_points=[s for e in report.get('exports',[]) for s in e.get('metrics',[])]
    identities=[(s.get('trace_id'),s.get('span_id')) for s in spans]
    check('platform.source_inventory',bool(spans) and len(identities)==len(set(identities)))
    check('platform.source_bounds',bool(spans) and all(type(s.get(k)) is int and
          window['from']*10**6<=s[k]<window['to']*10**6 for s in spans for k in ('start_time_unix_nano','end_time_unix_nano')))
    check('platform.source_three_signals',bool(spans) and bool(logs) and bool(metric_points))
    session=report.get('result',{}).get('session_id')
    check('platform.source_session',bool(session) and all(s.get('attributes',{}).get('gen_ai.conversation.id')==session for s in spans))
    if not all(checks.values()): return result()
    by_trace=defaultdict(list)
    for s in spans: by_trace[s['trace_id']].append(s)
    trace_ids=sorted(by_trace)
    prefix='/openapi/v1/spaces/'+urllib.parse.quote(str(target['space_id']),safe='')+'/agents/'+urllib.parse.quote(target['agent_product'],safe='')
    params={**window,'agent_uuid':target['agent_uuid'],'detail':'1'}
    filters=[{'field':'trace_id','op':'in','value':trace_ids}]
    deadline=time.monotonic()+max(0,min(poll_seconds,60))
    pagination_reads['native_traces']=[]
    rows,complete=_native_trace_inventory(query,target,prefix+'/traces/list',
        body={**window,'agent_uuid':target['agent_uuid'],'filters':filters},expected=set(trace_ids),
        deadline=deadline,proof=pagination_reads['native_traces'])
    ids=[r.get('trace_id') if isinstance(r,dict) else None for r in rows]
    check('platform.native.pagination',complete and len(ids)==len(set(ids)) and set(ids)==set(trace_ids))
    for i,(tid,own) in enumerate(sorted(by_trace.items())):
        native_detail_reads[tid]=[]
        while True:
            status,payload=query(target,prefix+'/trace/'+tid+'/detail',params=params)
            good,retryable=_native_detail_complete(own,status,payload,target)
            native=_data(payload) if isinstance(payload,dict) else None
            actual=native.get('spans') if isinstance(native,dict) else None
            native_detail_reads[tid].append({'status':_safe_page_fact(status),
                'code':_safe_page_fact(payload.get('code') if isinstance(payload,dict) else None),
                'span_count':len(actual) if isinstance(actual,list) else None,'expected_span_count':len(own),
                'ids':[{'trace_id':_safe_trace_id(span.get('trace_id') if isinstance(span,dict) else None),
                        'span_id':_safe_span_id(span.get('span_id') if isinstance(span,dict) else None)}
                       for span in actual[:200]] if isinstance(actual,list) else [],
                'exact':good,'exact_missing_subset':retryable,
                # Record only known server error categories, never arbitrary payload text.
                'error_kind': {
                    'Incomplete trace spans or storage row limit; narrow the time range': 'incomplete_trace',
                    'Invalid SigNoz response': 'invalid_response',
                    'Trace changed during detail read; retry': 'changed_trace',
                    'Invalid or shrinking trace count during detail read': 'invalid_count',
                    'Telemetry record not found': 'not_found',
                }.get(payload.get('message') if isinstance(payload,dict) and isinstance(payload.get('message'),str) else None)})
            if good or not retryable or time.monotonic()>=deadline: break
            time.sleep(min(1,max(0,deadline-time.monotonic())))
        check(f'platform.native.{i}.exact',good)
        status,public=query(target,'/api/public/traces/'+tid,basic=True)
        check(f'platform.v1.{i}.trace',status==200 and _v1_trace(own,public))
        rows,complete=_paged(query,target,'/api/public/observations',params={'traceId':tid},basic=True,limit=2)
        check(f'platform.v1.{i}.observations',complete and _v1_observations(own,rows))
        urls.append(str(target.get('ui_endpoint',target['endpoint'])).rstrip('/')+'/trace?'+urllib.parse.urlencode({
            'spaceId':target['space_id'],'agent':target['agent_product'],'agent_uuid':target['agent_uuid'],
            'traceId':tid,**window,'trace_from':window['from'],'trace_to':window['to']}))
    # Wait for the exact native inventory/detail boundary before reading the
    # public list. A trace ID alone can be visible before its terminal root is
    # queryable. Complete field mismatches still fail without being retried.
    rows,complete=_paged(query,target,'/api/public/traces',params={'sessionId':session,'fromTimestamp':_iso(window['from']*10**6),
                         'toTimestamp':_iso(window['to']*10**6)},basic=True,limit=1)
    ids=[r.get('id') for r in rows]
    pagination_reads['v1_traces']={'complete':complete,'ids':[_safe_trace_id(tid) for tid in ids],
        'expected_count':len(trace_ids),'row_count':len(rows)}
    check('platform.v1.trace_pagination',complete and len(ids)==len(set(ids)) and set(ids)==set(trace_ids)
          and all(_v1_trace(by_trace[r['id']],r,observations=False) for r in rows if r.get('id') in by_trace))
    body={**window,'agent_uuid':target['agent_uuid'],'sessionIds':[session]}
    for endpoint in ('usage','io'):
        status,payload=query(target,prefix+'/sessions/'+endpoint,body=body)
        container=payload.get('data',{}); rows=container.get('data',[]) if isinstance(container,dict) else container
        check('platform.session.'+endpoint+'.inventory',status==200 and payload.get('code',0)==0 and isinstance(rows,list) and len(rows)==1 and rows[0].get('id')==session)
        row=rows[0] if isinstance(rows,list) and len(rows)==1 else {}
        if endpoint=='usage': check('platform.session.usage.fields',_exact_fields(row.get('usage'),_totals(spans))
                                    and _known_parts_equal(row.get('usage',{}),spans))
        else:
            roots=sorted((s for s in spans if not s.get('parent_span_id')),key=lambda s:s['start_time_unix_nano'])
            check('platform.session.io.fields',bool(roots) and _decoded(row.get('inputValue'))==_io(roots[0],'input')
                  and _decoded(row.get('outputValue'))==_io(roots[-1],'output')
                  and row.get('inputSource',{}).get('traceId')==roots[0]['trace_id']
                  and row.get('outputSource',{}).get('traceId')==roots[-1]['trace_id'])
    status,public=query(target,'/api/public/sessions/'+urllib.parse.quote(session,safe=''),basic=True)
    session_traces=public.get('traces',[]) if isinstance(public,dict) else []
    ids=[r.get('id') for r in session_traces]
    check('platform.v1.session',status==200 and public.get('id')==session and public.get('projectId')==str(target['agent_uuid'])
          and len(ids)==len(set(ids)) and set(ids)==set(trace_ids)
          and all(_v1_trace(by_trace[r['id']],r,observations=False) for r in session_traces if r.get('id') in by_trace))
    calls=_calls(spans); known=[s for s in calls if _usage(s)['total'] is not None]
    for dimension in ('model','provider'):
        status,payload=query(target,prefix+'/traces/metrics',body={**window,'agent_uuid':target['agent_uuid'],'filters':filters,'bucket':'minute','groupBy':dimension})
        actual=_data(payload)
        check('platform.trace_metrics.'+dimension+'.http',status==200 and payload.get('code',0)==0 and isinstance(actual,dict))
        if not isinstance(actual,dict): actual={}
        check('platform.trace_metrics.'+dimension+'.inventory',_exact_fields(actual,{'traceCount':len(by_trace),'spanCount':len(spans),
              'generationCount':sum(_kind(s)=='GENERATION' for s in spans),'toolCount':sum(_kind(s)=='TOOL' for s in spans)}))
        check('platform.trace_metrics.'+dimension+'.usage',_exact_fields(actual.get('usage'),_totals(spans))
              and _known_parts_equal(actual.get('usage',{}),spans))
        check('platform.trace_metrics.'+dimension+'.reported',_reported_equal(actual.get('reportedUsage'),_reported(spans)))
        check('platform.trace_metrics.'+dimension+'.coverage',_exact_fields(actual.get('coverage'),{'billableCalls':len(calls),
              'knownUsageCalls':len(known),'knownTokens':sum(_usage(s)['total'] for s in known)}))
        wanted_groups=defaultdict(list)
        for s in calls:
            a=s['attributes']; value=a.get('gen_ai.request.model') if dimension=='model' else a.get('gen_ai.provider.name',a.get('gen_ai.system'))
            wanted_groups[value or None].append(s)
        groups=actual.get('groups',[]); values=[g.get('value') for g in groups]
        check('platform.trace_metrics.'+dimension+'.groups',len(values)==len(set(values)) and set(values)==set(wanted_groups)
              and all(g.get('dimension')==dimension and type(g.get('callCount')) is int and g['callCount']==len(wanted_groups[g['value']])
                      and _exact_fields(g.get('usage'),_totals(wanted_groups[g['value']]))
                      and _known_parts_equal(g.get('usage',{}),wanted_groups[g['value']]) for g in groups if g.get('value') in wanted_groups))
        typed=actual.get('usageByType',[]); kinds=[r.get('type') for r in typed]; expected_kinds={_kind(s) for s in calls}
        check('platform.trace_metrics.'+dimension+'.by_type',len(kinds)==len(set(kinds)) and set(kinds)==expected_kinds
              and all(type(r.get('callCount')) is int and r['callCount']==sum(_kind(s)==r['type'] for s in calls)
                      and _exact_fields(r.get('usage'),_totals([s for s in calls if _kind(s)==r['type']]))
                      and _known_parts_equal(r.get('usage',{}),[s for s in calls if _kind(s)==r['type']])
                      and _reported_equal(r.get('reportedUsage'),_reported([s for s in calls if _kind(s)==r['type']])) for r in typed))
    rows,complete=_paged(query,target,prefix+'/events/list',body={**window,'agent_uuid':target['agent_uuid'],
        'filters':[{'field':'session_id','op':'=','value':session}],
        'fields':['time','event_name','trace_id','span_id','session_id','body'],'orderBy':'time'},limit=1)
    expected_events=Counter((s.get('trace_id') or None,s.get('span_id') or None,_event_name(s),
                             _iso(s.get('time_unix_nano')),_event_body(s.get('body'))) for s in logs)
    actual_events=Counter((r.get('trace_id') or None,r.get('span_id') or None,r.get('event_name'),r.get('time'),_event_body(r.get('body'))) for r in rows)
    check('platform.events.exact',complete and actual_events==expected_events and all(r.get('session_id')==session for r in rows))
    configured=bool((target.get('dashboard_key') or target.get('dashboard_headers')) and target.get('metric_query_key'))
    check('platform.otel_metrics.configured',configured)
    if configured:
        metric_params={'agent_product':target['agent_product'],'data_item':target['metric_query_key'],
                       'fromTimestamp':_iso(window['from']*10**6),'toTimestamp':_iso(window['to']*10**6)}
        path='/api/v1/spaces/'+urllib.parse.quote(str(target['space_id']),safe='')+'/query'
        headers=target.get('dashboard_headers') or {'Authorization':'Bearer '+target['dashboard_key']}
        status,payload=query(target,path,params=metric_params,headers=headers)
        actual=payload.get('data',{})
        if not isinstance(actual,dict): actual={}
        count_points=[point for point in metric_points if point.get('name')=='agent.api_request.count']
        # This registered sum/sum query sums intervals. A cumulative capture
        # needs a different query contract even if its scalar happens to match.
        delta_contract=bool(count_points) and all(point.get('kind')=='sum'
            and type(point.get('temporality')) is int and point['temporality']==1 for point in count_points)
        check('platform.otel_metrics.delta_contract',delta_contract)
        try: expected_count=metric_count(count_points,'agent.api_request.count')
        except ValueError: expected_count=None
        scalar=actual.get('data',{}) if isinstance(actual,dict) else {}
        value=scalar.get('value') if isinstance(scalar,dict) and set(scalar)=={'value'} else None
        metric_read.update(status=_safe_page_fact(status), code=_safe_page_fact(payload.get('code',0)),
            value_type='number' if actual.get('value_type')=='number' else 'unexpected',
            value=value if type(value) in (int,float) and math.isfinite(value) else {'invalid_type':type(value).__name__},
            expected=expected_count, delta_contract=delta_contract)
        check('platform.otel_metrics.physical_count',status==200 and payload.get('code',0)==0
              and actual.get('value_type')=='number' and type(value) in (int,float)
              and value==expected_count and expected_count is not None and expected_count>0)
    path=prefix+'/trace/'+trace_ids[0]+'/detail'
    isolation=(('invalid_auth',path,params,'sk-aiv-invalid-native-e2e',401),
               ('cross_space',path.replace('/spaces/'+str(target['space_id'])+'/', '/spaces/'+str(uuid.uuid4())+'/'),params,None,403),
               ('cross_agent',path,{**params,'agent_uuid':str(uuid.uuid4())},None,400),
               ('wrong_window',path,{**params,'from':window['to']+3600000,'to':window['to']+3601000},None,404))
    for name,path,values,key,expected_status in isolation:
        status,payload=query(target,path,params=values,key=key)
        data=_data(payload)
        check('platform.isolation.'+name,status==expected_status and not (data.get('spans') if isinstance(data,dict) else data))
    return result()
