"""Runner contracts: evidence fidelity and cleanup before SDK shutdown."""
import asyncio
import os
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, ToolMessage
import execute_rag
from execute_rag import message_record, usage_totals, clear_environment, close_resources, build_agent
from rag_fixture import RAGFixture

class RunnerTests(unittest.TestCase):
    def test_public_identity_cannot_export_private_auth_or_future_target_fields(self):
        project = getattr(execute_rag, 'public_identity', None)
        self.assertTrue(callable(project), 'Runner needs an explicit public identity projection')
        identity = project({'endpoint': 'http://127.0.0.1:8001', 'agent_uuid': 'agent', 'space_id': 'space',
                            'agent_product': 'langfuse', 'data_backend': 'oceanbase',
                            'authenticated_user_id': 'user', 'space_key': 'do-not-export',
                            'dashboard_headers': {'Cookie': 'do-not-export'}, 'ingest_token': 'do-not-export',
                            'future_private_config': {'secret': 'do-not-export'}})
        self.assertEqual(identity, {'endpoint': 'http://127.0.0.1:8001', 'base_url': 'http://127.0.0.1:8001',
                                    'agent_uuid': 'agent', 'space_id': 'space', 'agent_product': 'langfuse',
                                    'data_backend': 'oceanbase', 'authenticated_user_id': 'user'})
    def test_preserve_tool_link_and_explicit_zero(self):
        tool=ToolMessage(content='{"storage_gib":140}',tool_call_id='actual-call',name='calculate_storage',status='success')
        self.assertEqual(message_record(tool)['tool_call_id'],'actual-call')
        self.assertEqual(message_record(tool)['content'],'{"storage_gib":140}')
        self.assertEqual(usage_totals([{'kind':'model_completed','usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}]), {'input_tokens':0,'output_tokens':0,'total_tokens':0})
        self.assertEqual(usage_totals([{'kind':'model_completed','usage':None}]), {'input_tokens':None,'output_tokens':None,'total_tokens':None})

    def test_no_inherited_provider_or_tracing_config(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'dummy','LANGFUSE_HOST':'https://bad.invalid','OTEL_EXPORTER_OTLP_ENDPOINT':'https://bad.invalid','AIVISION_E2E_TARGETS_JSON':'{}'}, clear=True):
            clear_environment()
            self.assertNotIn('OPENAI_API_KEY',os.environ)
            self.assertNotIn('LANGFUSE_HOST',os.environ)
            self.assertNotIn('OTEL_EXPORTER_OTLP_ENDPOINT',os.environ)
            self.assertEqual(os.environ['AIVISION_E2E_TARGETS_JSON'],'{}')

    def test_capture_closed_when_sdk_shutdown_fails(self):
        events=[]
        class SDK:
            def close(self):
                events.append('sdk'); raise RuntimeError('flush failed')
        class Capture:
            def close(self): events.append('capture')
        with self.assertRaisesRegex(RuntimeError,'flush failed'):
            close_resources(SDK(),Capture())
        self.assertEqual(events,['sdk','capture'])

    def test_real_graph_checkpoint_does_not_repeat_tools(self):
        async def run():
            fixture=RAGFixture(receipt='offline-receipt')
            graph=build_agent('langchain','offline',fixture)
            config={'configurable':{'thread_id':'offline-checkpoint'}}
            first=await graph.ainvoke({'messages':[{'role':'user','content':'Use the policy to calculate daily storage of 10 GiB.'}]},config)
            count=len([r for r in fixture.ledger if r['kind']=='tool_result'])
            second=await graph.ainvoke({'messages':[{'role':'user','content':'Follow up: return the previous answer without tools.'}]},config)
            self.assertEqual(second['messages'][-1].content,first['messages'][-1].content)
            self.assertEqual(len([r for r in fixture.ledger if r['kind']=='tool_result']),count)
            self.assertEqual(count,2)
        asyncio.run(run())

if __name__=='__main__': unittest.main()
