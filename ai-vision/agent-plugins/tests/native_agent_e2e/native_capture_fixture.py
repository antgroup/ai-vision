"""Test-only OTLP serialization for literal independent native oracle fixtures."""
from copy import deepcopy
import json

from capture import decode_export


def _value(value):
    if type(value) is int: return {'intValue':str(value)}
    if type(value) is bool: return {'boolValue':value}
    if type(value) is float: return {'doubleValue':value}
    if isinstance(value,str): return {'stringValue':value}
    if isinstance(value,list): return {'arrayValue':{'values':[_value(v) for v in value]}}
    raise ValueError('Unsupported fixture attribute')


def _attrs(value): return [{'key':key,'value':_value(item)} for key,item in value.items()]


def captured_native(export):
    export=deepcopy(export)
    ids={'t':'a'*32,'root':'1'*16,'llm1':'2'*16,'tool':'3'*16,'llm2':'4'*16}
    for row in export['spans']+export['logs']:
        for key in ('trace_id','span_id','parent_span_id'):
            if key in row: row[key]=ids.get(row[key],row[key])
    output=[]
    for signal,key,root,scope,rows in (('traces','spans','resourceSpans','scopeSpans','spans'),
                                      ('logs','logs','resourceLogs','scopeLogs','logRecords'),
                                      ('metrics','metrics','resourceMetrics','scopeMetrics','metrics')):
        resources=[]
        for row in export[key]:
            if signal=='traces':
                item={'name':row.get('name',''),'traceId':row['trace_id'],'spanId':row['span_id'],'parentSpanId':row.get('parent_span_id',''),
                    'startTimeUnixNano':str(row['start_time_unix_nano']),'endTimeUnixNano':str(row['end_time_unix_nano']),
                    'attributes':_attrs(row['attributes']),'status':row['status']}
            elif signal=='logs':
                item={'traceId':row['trace_id'],'spanId':row['span_id'],'attributes':_attrs(row['attributes']),
                    'body':_value(row.get('body','api_request')),'timeUnixNano':str(row.get('time_unix_nano',1))}
            else:
                point={**row['point'],'attributes':_attrs(row['attributes'])}
                item={'name':row['name'],row['kind']:{'aggregationTemporality':row['temporality'],'dataPoints':[point]}}
            resources.append({'resource':{'attributes':_attrs(row['resource'])},scope:[{
                'scope':{'name':row.get('scope_name','observ-dsh'),'version':row.get('scope_version','')},rows:[item]}]})
        payload={root:resources}; decoded=decode_export(signal,json.dumps(payload).encode(),'application/json'); decoded['admitted']=True
        output.append(decoded)
    return json.loads(json.dumps(output))
