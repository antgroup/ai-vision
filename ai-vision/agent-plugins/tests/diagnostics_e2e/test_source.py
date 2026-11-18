import json
import unittest
import tempfile
from copy import deepcopy
from pathlib import Path
from source import source_proof, expectations, validate_projection
from scenarios import scenarios

class RagSourceTests(unittest.TestCase):
    def test_document_tool_generation_and_projection_are_all_required(self):
        from source import rag_material_proof
        scenario = next(case for case in scenarios(include_rag=True) if case.name == 'rag-grounded')
        document = scenario.files['retrieved.txt']
        inputs = [{'role': 'user', 'content': scenario.prompt},
                  {'role': 'tool', 'tool_call_id': 'read-1', 'content': document}]
        final = 'Public natural answer; content correctness belongs to human review.'
        tool = {'trace_id': 'a'*32, 'span_id': '1'*16, 'end_time_unix_nano': 20,
            'attributes': {'gen_ai.operation.name': 'execute_tool', 'gen_ai.tool.call.id': 'read-1',
                'gen_ai.tool.name': 'read', 'gen_ai.tool.call.arguments': '{"file_path":"retrieved.txt"}',
                'gen_ai.tool.call.result': document}}
        llm = {'trace_id': 'a'*32, 'span_id': '2'*16, 'start_time_unix_nano': 21,
            'attributes': {'gen_ai.span.kind': 'LLM', 'gen_ai.input.messages': json.dumps(inputs),
                'gen_ai.output.messages': json.dumps({'role':'assistant','content':final})}}
        report = {'app':'codex', 'exports':[{'admitted':True, 'spans':[tool,llm]}],
            'result':{'details':{'native_transcript':[{'type':'event_msg','payload':{
                'type':'task_complete','last_agent_message':final}}]}},
            'requests':[{'completed':True,'request':{'messages':inputs},
                'response':{'content':final}}]}
        projection = {'provided_context':[{'kind':'tool_result','content':document,'sources':[{
            'trace_id':'a'*32,'span_id':'2'*16,'call_id':'read-1',
            'field_path':'attributes.gen_ai.input.messages[1].content'}], 'producer_source':{
            'trace_id':'a'*32,'span_id':'1'*16,'call_id':'read-1',
            'field_path':'attributes.gen_ai.tool.call.result'}}]}
        self.assertTrue(rag_material_proof(report, scenario, projection=projection)['verified'])
        for mutation in ('file_only','instruction','ordering','wrong_path','wrong_content','projection'):
            changed, view = deepcopy(report), deepcopy(projection)
            if mutation == 'file_only': changed['exports'][0]['spans'] = [tool]
            elif mutation == 'instruction':
                values = deepcopy(inputs); values[1]['role'] = 'system'
                changed['exports'][0]['spans'][1]['attributes']['gen_ai.input.messages'] = json.dumps(values)
            elif mutation == 'ordering': changed['exports'][0]['spans'][0]['end_time_unix_nano'] = 30
            elif mutation == 'wrong_path': changed['exports'][0]['spans'][0]['attributes']['gen_ai.tool.call.arguments'] = '{"file_path":"other.txt"}'
            elif mutation == 'wrong_content': changed['requests'][0]['request']['messages'][1]['content'] = 'invented'
            else: view['provided_context'][0]['sources'][0]['span_id'] = '3'*16
            with self.subTest(mutation=mutation):
                self.assertFalse(rag_material_proof(changed, scenario, projection=view)['verified'])

class RagReadEvidenceTests(unittest.TestCase):
    """Exercise reference delivery with native, unmodified tool result formats."""
    def world(self, name, arguments, result, *, case='rag-insufficient-evidence', app='codex'):
        scenario = next(item for item in scenarios(include_rag=True) if item.name == case)
        final = 'The provided material does not establish a retention duration.'
        messages = [{'role': 'user', 'content': scenario.prompt},
                    {'role': 'tool', 'tool_call_id': 'physical-read', 'content': result}]
        tool = {'trace_id': 'a'*32, 'span_id': '1'*16, 'end_time_unix_nano': 20,
            'attributes': {'gen_ai.operation.name': 'execute_tool',
                'gen_ai.tool.name': name, 'gen_ai.tool.call.id': 'physical-read',
                'gen_ai.tool.call.arguments': json.dumps(arguments),
                'gen_ai.tool.call.result': result}}
        llm = {'trace_id': 'a'*32, 'span_id': '2'*16, 'start_time_unix_nano': 21,
            'attributes': {'gen_ai.span.kind': 'LLM', 'gen_ai.input.messages': json.dumps(messages),
                'gen_ai.output.messages': json.dumps({'role': 'assistant', 'content': final})}}
        details = {'native_transcript': [{'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'last_agent_message': final}}]}
        if app == 'dsh02':
            details = {'canonical_events': [{'type': 'assistant/message', 'data': {
                'message': {'content': [{'type': 'text', 'text': final}]}}}]}
        report = {'app': app, 'exports': [{'admitted': True, 'spans': [tool, llm]}],
            'result': {'details': details}, 'requests': [{'completed': True,
                'request': {'messages': messages}, 'response': {'content': final}}]}
        projection = {'provided_context': [{'kind': 'tool_result', 'content': result,
            'sources': [{'trace_id': 'a'*32, 'span_id': '2'*16, 'call_id': 'physical-read',
                'field_path': 'attributes.gen_ai.input.messages[1].content'}],
            'producer_source': {'trace_id': 'a'*32, 'span_id': '1'*16,
                'call_id': 'physical-read', 'field_path': 'attributes.gen_ai.tool.call.result'}}]}
        return report, scenario, projection

    def proof(self, name, arguments, result, **kwargs):
        from source import rag_material_proof
        workspace = kwargs.pop('workspace', None)
        report, scenario, projection = self.world(name, arguments, result, **kwargs)
        return rag_material_proof(report, scenario, workspace=workspace, projection=projection)

    def test_codex_rg_line_numbered_context_reaches_the_actual_final_generation(self):
        output = ('1-Public retrieved policy\n2:project=orion\n3-owner=operations\n'
                  '4:Retention duration is not provided in this document.\n')
        wrapped = ('Chunk ID: abc123\nWall time: 0.02 seconds\n'
                   'Process exited with code 0\nFinal output:\n' + output)
        for result in (output, wrapped):
            with self.subTest(wrapped=result == wrapped):
                self.assertTrue(self.proof('exec_command', {
                    'cmd': "rg -ni -C 3 'retention|orion|duration' retrieved.txt",
                    'workdir': '/isolated/workspace'}, result)['verified'])

    def test_rg_exact_nonempty_excerpt_does_not_require_full_document_coverage(self):
        self.assertTrue(self.proof('tool Bash', {
            'command': "rg -n 'retention' ./retrieved.txt"},
            '4:Retention duration is not provided in this document.\n')['verified'])

    def test_rg_rejects_invented_lines_and_inconsistent_numbering(self):
        outputs = ('4:retention_days=14\n', '3:project=orion\n',
            '2:project=orion\n4:invented retention policy\n',
            '4:Retention duration is not provided in this document.\n2:project=orion\n',
            '2:project=orion\n2:project=orion\n', 'project=orion\n',
            'retrieved.txt\n', 'other.txt:2:project=orion\n', '1:Public retrieved\n',
            '9'*5000 + ':project=orion\n', '', '--\n2:project=orion\n')
        for result in outputs:
            with self.subTest(result=result):
                self.assertFalse(self.proof('exec_command', {
                    'cmd': "rg -n 'orion|retention' retrieved.txt"}, result)['verified'])

    def test_read_evidence_rejects_shell_composition_expansion_and_non_read_commands(self):
        document = 'Public retrieved policy\nproject=orion\nowner=operations\nRetention duration is not provided in this document.\n'
        commands = ('cat other.txt | cat retrieved.txt', 'cat retrieved.txt && cat retrieved.txt',
            'cat retrieved.txt; cat retrieved.txt', 'cat retrieved.txt\ncat retrieved.txt',
            'cat $(echo retrieved.txt)', 'cat `echo retrieved.txt`',
            'cat < retrieved.txt', 'echo retrieved.txt',
            "awk 'BEGIN {print \"Public retrieved policy\"}' retrieved.txt",
            "sed 'a invented policy' retrieved.txt", 'rg -l orion retrieved.txt',
            'rg -n orion other.txt', 'rg -n orion retrieved.txt other.txt',
            'rg --replace invented -n orion retrieved.txt')
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.proof('exec_command', {'cmd': command}, document)['verified'])

    def test_rg_cannot_use_shell_expansion_to_inject_a_preprocessor_or_change_arguments(self):
        output = '4:Retention duration is not provided in this document.\n'
        commands = ('rg -n {retention,--pre=/inert/emit_document} retrieved.txt',
            'rg -n retention* retrieved.txt', 'rg -n ?etention retrieved.txt',
            'rg -n [r]etention retrieved.txt', 'rg -n ~ retrieved.txt',
            'rg -n @(retention) retrieved.txt', 'rg -n !(orion) retrieved.txt')
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.proof('exec_command', {'cmd': command}, output)['verified'])

    def test_cat_full_document_and_known_success_output_wrapper_remain_supported(self):
        document = 'Public retrieved policy\nproject=orion\nowner=operations\nRetention duration is not provided in this document.\n'
        for result in (document, 'Chunk ID: abc123\nWall time: 0.02 seconds\nProcess exited with code 0\nOutput:\n' + document):
            with self.subTest(result=result):
                self.assertTrue(self.proof('exec_command', {'cmd': 'cat -- retrieved.txt'}, result)['verified'])

    def test_codex_original_token_count_metadata_is_a_known_optional_envelope_field(self):
        output = ('Chunk ID: 1d82f0\nWall time: 0.0000 seconds\nProcess exited with code 0\n'
                  'Original token count: 14\nOutput:\nPublic retrieved policy\nproject=orion\nretention_days=14\n')
        self.assertTrue(self.proof('tool Bash', {'command': 'cat retrieved.txt'}, output,
            case='rag-grounded')['verified'])
        for metadata in ('Original token count: invented', 'Original token count: -14',
                         'Original token count: 14\ninvented envelope line'):
            with self.subTest(metadata=metadata):
                self.assertFalse(self.proof('tool Bash', {'command': 'cat retrieved.txt'},
                    output.replace('Original token count: 14', metadata), case='rag-grounded')['verified'])

    def test_known_shell_wrapper_cannot_hide_fabricated_output_or_nonzero_exit(self):
        document = 'Public retrieved policy\nproject=orion\nowner=operations\nRetention duration is not provided in this document.\n'
        outputs = ('invented prefix\n' + document, document + 'invented suffix\n',
            'Chunk ID: abc123\nProcess exited with code 1\nOutput:\n' + document)
        for result in outputs:
            with self.subTest(result=result):
                self.assertFalse(self.proof('exec_command', {'cmd': 'cat retrieved.txt'}, result)['verified'])

    def test_head_and_tail_exact_line_excerpts_are_supported_without_substring_matching(self):
        for command, result in (('head -n 2 retrieved.txt', 'Public retrieved policy\nproject=orion\n'),
            ('tail -n 1 retrieved.txt', 'Retention duration is not provided in this document.\n')):
            with self.subTest(command=command):
                self.assertTrue(self.proof('exec_command', {'cmd': command}, result)['verified'])
                self.assertFalse(self.proof('exec_command', {'cmd': command}, 'project=ori\n')['verified'])

    def test_dsh_file_renderer_preserves_absolute_workspace_path_and_exact_lines(self):
        for case, content, total in (
            ('rag-grounded', '1: Public retrieved policy\n2: project=orion\n3: retention_days=14', 3),
            ('rag-insufficient-evidence', '1: Public retrieved policy\n2: project=orion\n3: owner=operations\n4: Retention duration is not provided in this document.', 4)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                document = next(item for item in scenarios(include_rag=True) if item.name == case).files['retrieved.txt']
                path = Path(directory) / 'retrieved.txt'
                path.write_text(document)
                output = f'<path>{path}</path>\n<type>file</type>\n<content>\n{content}\n\n(End of file - total {total} lines)\n</content>'
                self.assertTrue(self.proof('read', {'file_path': 'retrieved.txt'}, output,
                    case=case, app='dsh02', workspace=directory)['verified'])
                wrong_path = output.replace(f'<path>{path}</path>', '<path>/other/workspace/retrieved.txt</path>')
                self.assertFalse(self.proof('read', {'file_path': 'retrieved.txt'}, wrong_path,
                    case=case, app='dsh02', workspace=directory)['verified'])

    def test_dsh_renderer_rejects_wrong_path_fabrication_and_invalid_line_metadata(self):
        content = '1: Public retrieved policy\n2: project=orion\n3: owner=operations\n4: Retention duration is not provided in this document.'
        output = f'<path>retrieved.txt</path>\n<type>file</type>\n<content>\n{content}\n\n(End of file - total 4 lines)\n</content>'
        outputs = (output.replace('<path>retrieved.txt', '<path>other.txt'),
            output.replace('owner=operations', 'owner=forged'), output.replace('2: project', '3: project'),
            output.replace('total 4 lines', 'total 99 lines'), output.replace('<type>file', '<type>directory'),
            output + '\ninvented footer', output.replace('1: Public retrieved policy', '1: Public retrieved'),
            output.replace('total 4 lines', 'total ' + '9'*5000 + ' lines'))
        for result in outputs:
            with self.subTest(result=result):
                self.assertFalse(self.proof('read', {'file_path': 'retrieved.txt'}, result, app='dsh02')['verified'])

    def test_rendered_evidence_still_requires_original_result_and_all_causal_bindings(self):
        from source import rag_material_proof
        output = '4:Retention duration is not provided in this document.\n'
        baseline, scenario, view = self.world('exec_command', {'cmd': 'rg -n retention retrieved.txt'}, output)
        original_report, original_projection = deepcopy(baseline), deepcopy(view)
        self.assertTrue(rag_material_proof(baseline, scenario, projection=view)['verified'])
        self.assertEqual(baseline, original_report)
        self.assertEqual(view, original_projection)
        for mutation in ('consumer_call', 'wire_result', 'wire_call', 'wire_completed',
                         'tool_call', 'ordering', 'producer', 'projection_result', 'consumer_span'):
            report, projection = deepcopy(baseline), deepcopy(view)
            tool, llm = report['exports'][0]['spans']
            if mutation == 'consumer_call':
                messages = json.loads(llm['attributes']['gen_ai.input.messages'])
                messages[1]['tool_call_id'] = 'unrelated'
                llm['attributes']['gen_ai.input.messages'] = json.dumps(messages)
            elif mutation == 'wire_result': report['requests'][0]['request']['messages'][1]['content'] = output.strip()
            elif mutation == 'wire_call': report['requests'][0]['request']['messages'][1]['tool_call_id'] = 'unrelated'
            elif mutation == 'wire_completed': report['requests'][0]['completed'] = False
            elif mutation == 'tool_call': tool['attributes']['gen_ai.tool.call.id'] = 'unrelated'
            elif mutation == 'ordering': tool['end_time_unix_nano'] = 22
            elif mutation == 'producer': projection['provided_context'][0]['producer_source']['span_id'] = '3'*16
            elif mutation == 'projection_result': projection['provided_context'][0]['content'] = output.strip()
            else: projection['provided_context'][0]['sources'][0]['span_id'] = '3'*16
            with self.subTest(mutation=mutation):
                self.assertFalse(rag_material_proof(report, scenario, projection=projection)['verified'])

class SourceTests(unittest.TestCase):
    def test_predefined_app_expectations(self):
        case=next(s for s in scenarios() if s.name=='sensitive-read')
        rules,skips=expectations('codex',case)
        self.assertEqual(rules['high_risk_command'],'bad')
        self.assertNotIn('high_risk_command',skips)
        rules,skips=expectations('dsh02',case)
        self.assertNotIn('high_risk_command',rules)
        self.assertEqual(skips['high_risk_command'],'not_applicable')
        self.assertEqual(skips['user_interruption'],'missing_required_evidence')
    def test_missing_native_source_never_passes(self):
        value=source_proof(result={'exit_code':0,'session_id':'s'},exports=[],requests=[],scenario=scenarios()[0],mode='fake',workspace=Path('/unused'))
        self.assertFalse(value['verified'])
        self.assertIn('native.root_trace',value['failures'])
    def test_rejected_wire_cannot_be_diagnosed(self):
        span={'trace_id':'a'*32,'span_id':'b'*16,'parent_span_id':'','name':'Turn 1','start_time_unix_nano':1,'end_time_unix_nano':2}
        value=source_proof(result={'exit_code':0,'session_id':'s'},exports=[{'signal':'traces','admitted':False,'spans':[span]}],requests=[],scenario=scenarios()[0],mode='fake',workspace=Path('/unused'))
        self.assertFalse(value['verified'])
        self.assertIn('native.root_trace',value['failures'])
class CausalSourceTests(unittest.TestCase):
    def world(self):
        resource={'ant.agent.instance':'run-unique'}
        base={'trace_id':'a'*32,'resource':resource,'start_time_unix_nano':20,'end_time_unix_nano':40}
        spans=[{**base,'span_id':'1'*16,'parent_span_id':'','name':'Turn 1',
                'attributes':{'gen_ai.conversation.id':'native-session','gen_ai.operation.name':'invoke_agent'}},
            {**base,'span_id':'2'*16,'parent_span_id':'1'*16,'name':'chat fake',
                'attributes':{'gen_ai.conversation.id':'native-session','gen_ai.operation.name':'chat'}},
            {**base,'span_id':'3'*16,'parent_span_id':'1'*16,'name':'tool read',
                'attributes':{'gen_ai.conversation.id':'native-session','gen_ai.operation.name':'execute_tool',
                    'gen_ai.tool.call.id':'call-1','gen_ai.tool.name':'read',
                    'gen_ai.tool.call.arguments':'{}','gen_ai.tool.call.result':'return a + b'}}]
        rows=[{'purpose':'agent','completed':True,'response':{'tool_calls':[{'id':'call-1','function':{'name':'read','arguments':'{}'}}]},'request':{'messages':[]}},
              {'purpose':'agent','completed':True,'request':{'messages':[{'role':'tool','tool_call_id':'call-1','content':'return a + b'}]}}]
        return dict(result={'exit_code':0,'session_id':'native-session'},exports=[{'admitted':True,'spans':spans}],requests=rows,
                    scenario=scenarios()[0],mode='fake',workspace=Path('/unused'),bounds=(10,100),resource=resource)
    def test_current_session_tool_id_clock_and_result_all_required(self):
        from copy import deepcopy
        baseline=self.world()
        self.assertTrue(source_proof(**baseline)['verified'])
        for mutation in ('session','clock','tool','result','resource'):
            changed=deepcopy(baseline)
            spans=changed['exports'][0]['spans']
            if mutation=='session': spans[0]['attributes']['gen_ai.conversation.id']='unrelated'
            elif mutation=='clock': spans[0]['start_time_unix_nano']=1
            elif mutation=='tool': changed['exports'][0]['spans']=spans[:2]
            elif mutation=='result': spans[2]['attributes']['gen_ai.tool.call.result']='fabricated success'
            else: spans[0]['resource']={'ant.agent.instance':'different run'}
            with self.subTest(mutation=mutation):
                self.assertFalse(source_proof(**changed)['verified'])

    def test_provider_name_and_typed_arguments_bind_original_tool_call(self):
        baseline = self.world()
        original = {'file_path': 'app.py', 'confirmed': True, 'text': 'keep whitespace\n'}
        baseline['requests'][0]['response']['tool_calls'][0]['function']['arguments'] = json.dumps(original)
        attrs = baseline['exports'][0]['spans'][-1]['attributes']
        attrs['gen_ai.tool.call.arguments'] = json.dumps(original)
        self.assertTrue(source_proof(**baseline)['verified'])
        for mutation in ('name', 'path', 'boolean', 'whitespace', 'missing'):
            changed = deepcopy(baseline)
            attrs = changed['exports'][0]['spans'][-1]['attributes']
            arguments = deepcopy(original)
            if mutation == 'name': attrs['gen_ai.tool.name'] = 'bash'
            elif mutation == 'path': arguments['file_path'] = 'unrelated.py'
            elif mutation == 'boolean': arguments['confirmed'] = 1
            elif mutation == 'whitespace': arguments['text'] = arguments['text'].rstrip()
            else: attrs.pop('gen_ai.tool.call.arguments')
            if mutation != 'missing': attrs['gen_ai.tool.call.arguments'] = json.dumps(arguments)
            with self.subTest(mutation=mutation):
                self.assertFalse(source_proof(**changed)['verified'])

    def test_codex_canonical_shell_and_custom_patch_mappings_are_explicit(self):
        import json
        for name, issued, exported_name, exported in (
                ('exec_command', {'cmd': 'cat app.py', 'workdir': '/fixture'}, 'tool Bash', {'command': 'cat app.py'}),
                ('apply_patch', {'input': '*** Begin Patch\n*** End Patch\n'}, 'tool apply_patch', '*** Begin Patch\n*** End Patch\n')):
            baseline = self.world()
            baseline['resource']['ant.agent.type'] = 'codex'
            for span in baseline['exports'][0]['spans']: span['resource'] = deepcopy(baseline['resource'])
            baseline['requests'][0]['response']['tool_calls'][0]['function'] = {'name': name, 'arguments': json.dumps(issued)}
            attrs = baseline['exports'][0]['spans'][-1]['attributes']
            attrs.update({'gen_ai.tool.name': exported_name, 'gen_ai.tool.call.arguments': json.dumps(exported)})
            with self.subTest(name=name):
                self.assertTrue(source_proof(**baseline)['verified'])
                attrs['gen_ai.tool.call.arguments'] = json.dumps({'command': 'cat unrelated.py'})
                self.assertFalse(source_proof(**baseline)['verified'])

    def test_privacy_input_requires_the_canonical_redaction_marker(self):
        for arguments in ('[REDACTED]', json.dumps('[REDACTED]'), '{}', 'unrelated redaction'):
            baseline = self.world()
            baseline['scenario'] = next(s for s in scenarios() if s.name == 'privacy')
            attrs = baseline['exports'][0]['spans'][-1]['attributes']
            attrs['gen_ai.tool.call.arguments'] = arguments
            attrs['gen_ai.tool.call.result'] = '[REDACTED]'
            with self.subTest(arguments=arguments):
                self.assertEqual(source_proof(**baseline)['verified'], 'REDACTED' in arguments)

class ProjectionTests(unittest.TestCase):
    def world(self, app='codex'):
        scenario=next(s for s in scenarios() if s.name=='read-only')
        final='add(2, 3) returns 5.'
        spans=[{'trace_id':'a'*32,'span_id':'1'*16,'name':'Turn 1',
                'attributes':{'gen_ai.span.kind':'CHAT','gen_ai.operation.name':'invoke_agent',
                    'gen_ai.output.messages':'[{"role":"assistant","content":"add(2, 3) returns 5."}]'}},
            {'trace_id':'a'*32,'span_id':'2'*16,'name':'chat fake',
                'attributes':{'gen_ai.span.kind':'LLM','gen_ai.operation.name':'chat'}},
            {'trace_id':'a'*32,'span_id':'3'*16,'name':'tool Bash',
                'attributes':{'gen_ai.span.kind':'TOOL','gen_ai.operation.name':'execute_tool',
                    'gen_ai.tool.call.id':'native-read','gen_ai.tool.name':'tool Bash',
                    'gen_ai.tool.call.arguments':'{ "command": "cat app.py" }',
                    'gen_ai.tool.call.result':'def add(a, b):\n    return a + b\n'}}]
        details={'native_transcript':[{'type':'event_msg','payload':{
            'type':'task_complete','last_agent_message':final}}], 'final_answers':[final]}
        projection={'trace_id':'a'*32,'runtime_type':'codex','projection_kind':'semantic',
            'user_query':scenario.prompt,'agent_output':final,'agent_output_span_id':'1'*16,
            'tool_actions':[{'action_id':'native-read','source_span_id':'3'*16,
                'call_id':'native-read','name':'Bash','raw_name':'tool Bash',
                'operation_name':'execute_tool','arguments':{'command':'cat app.py'},
                'result':'def add(a, b):\n    return a + b\n','status':'success'}],
            'file_mutations':[],'verification_actions':[]}
        if app=='dsh02':
            details={'canonical_events':[{'type':'assistant/message','seq':1,'data':{
                'message':{'role':'assistant','content':[{'type':'text','text':final}]}}}]}
            spans[-1]['attributes'].update({'gen_ai.tool.name':'read',
                'gen_ai.tool.call.arguments':'{"file_path":"app.py"}'})
            projection['runtime_type']='dsh'
            projection['tool_actions'][0].update(name='read',raw_name='read',arguments={'file_path':'app.py'})
        report={'app':app,'mode':'fake','source':{'source_trace_ids':['a'*32],
            'source_span_ids':['1'*16,'2'*16,'3'*16]},'exports':[{'admitted':True,'spans':spans}],
            'result':{'details':details}}
        return projection,report,scenario

    def test_real_tool_name_and_json_normalization_remain_valid(self):
        for app in ('codex','dsh02'):
            with self.subTest(app=app):
                self.assertTrue(validate_projection(*self.world(app))['verified'])
        projection,report,scenario=self.world()
        report['exports'][0]['spans'][-1]['attributes']['gen_ai.tool.call.result']='{"ok":true,"lines":[1,2]}'
        projection['tool_actions'][0]['result']={'lines':[1,2],'ok':True}
        self.assertTrue(validate_projection(projection,report,scenario)['verified'])

    def test_projection_tool_facts_must_match_the_referenced_execute_tool_span(self):
        projection,report,scenario=self.world()
        corruptions={'source_span_id':'1'*16,'call_id':'unrelated-call','name':'unrelated-tool',
            'operation_name':'chat','arguments':{'command':'echo unrelated'},
            'result':'invented success','action_id':'different-action'}
        for field,value in corruptions.items():
            changed=deepcopy(projection)
            changed['tool_actions'][0][field]=value
            with self.subTest(field=field):
                self.assertFalse(validate_projection(changed,report,scenario)['verified'])

    def test_source_ids_alone_cannot_substitute_for_original_span_evidence(self):
        projection,report,scenario=self.world()
        for mutation in ('missing_exports','rejected_export','wrong_trace','wrong_operation','wrong_kind'):
            changed=deepcopy(report)
            if mutation=='missing_exports': changed.pop('exports')
            elif mutation=='rejected_export': changed['exports'][0]['admitted']=False
            elif mutation=='wrong_trace': changed['exports'][0]['spans'][-1]['trace_id']='b'*32
            elif mutation=='wrong_operation': changed['exports'][0]['spans'][-1]['attributes']['gen_ai.operation.name']='chat'
            else: changed['exports'][0]['spans'][-1]['attributes']['gen_ai.span.kind']='LLM'
            with self.subTest(mutation=mutation):
                self.assertFalse(validate_projection(projection,changed,scenario)['verified'])

    def test_missing_and_duplicate_tool_actions_fail_in_real_model_mode(self):
        projection,report,scenario=self.world()
        report['mode']='real'
        for actions in ([], projection['tool_actions']*2):
            with self.subTest(count=len(actions)):
                self.assertFalse(validate_projection({**projection,'tool_actions':actions},report,scenario)['verified'])

    def test_missing_original_tool_material_cannot_pass_as_matching_none(self):
        projection,report,scenario=self.world()
        for field in ('arguments','result','call_id'):
            changed_projection=deepcopy(projection); changed_report=deepcopy(report)
            attribute='gen_ai.tool.call.'+('id' if field=='call_id' else field)
            changed_report['exports'][0]['spans'][-1]['attributes'].pop(attribute)
            changed_projection['tool_actions'][0][field]=None
            if field=='call_id': changed_projection['tool_actions'][0]['action_id']='3'*16
            with self.subTest(field=field):
                self.assertFalse(validate_projection(changed_projection,changed_report,scenario)['verified'])

    def test_final_answer_requires_raw_span_and_native_final_agreement(self):
        for app in ('codex','dsh02'):
            projection,report,scenario=self.world(app)
            for mutation in ('answer','source_id','raw_output','native_final','missing_native'):
                changed_projection=deepcopy(projection); changed_report=deepcopy(report)
                if mutation=='answer': changed_projection['agent_output']='Unrelated answer.'
                elif mutation=='source_id': changed_projection['agent_output_span_id']='3'*16
                elif mutation=='raw_output': changed_report['exports'][0]['spans'][0]['attributes']['gen_ai.output.messages']='Different output'
                elif mutation=='missing_native': changed_report['result']['details']={}
                elif app=='codex': changed_report['result']['details']['native_transcript'][0]['payload']['last_agent_message']='Different final'
                else: changed_report['result']['details']['canonical_events'][0]['data']['message']['content'][0]['text']='Different final'
                with self.subTest(app=app,mutation=mutation):
                    self.assertFalse(validate_projection(changed_projection,changed_report,scenario)['verified'])

    def test_privacy_compares_redacted_tool_facts_without_demanding_native_text(self):
        projection,report,_=self.world()
        scenario=next(s for s in scenarios() if s.name=='privacy')
        attrs=report['exports'][0]['spans'][-1]['attributes']
        attrs['gen_ai.tool.call.arguments']='[REDACTED]'; attrs['gen_ai.tool.call.result']='[REDACTED]'
        projection['tool_actions'][0].update(arguments='[REDACTED]',result='[REDACTED]')
        projection['user_query']='[REDACTED]'; projection['agent_output']='[REDACTED]'
        report['result']['details']={}
        self.assertTrue(validate_projection(projection,report,scenario)['verified'])
        projection['tool_actions'][0]['result']='unrelated redaction'
        self.assertFalse(validate_projection(projection,report,scenario)['verified'])

    def test_dsh_intermediate_message_cannot_be_promoted_to_native_final(self):
        projection,report,scenario=self.world('dsh02')
        report['result']['details']['canonical_events'].append({'type':'assistant/message','seq':2,
            'data':{'message':{'role':'assistant','content':[{'type':'tool-call',
                'id':'native-next','name':'read','arguments':'{}'}]}}})
        self.assertFalse(validate_projection(projection,report,scenario)['verified'])

    def test_targets_are_validated_before_network_or_credentials(self):
        from source import validate_targets
        for value in ({}, {'codex':{'data_backend':'sqlite'}},
                      {'codex':{'data_backend':'oceanbase','endpoint':'https://remote.example'}}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_targets(value,['codex'])

if __name__=='__main__': unittest.main()
