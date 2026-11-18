import test from 'node:test';
import assert from 'node:assert/strict';
import type { Span } from '@opentelemetry/api';
import { setModelCallContent } from '../src/hooks/model-call-content.js';
import { softTruncateMessages } from '../src/hooks/truncate.js';
for(const mime of ['image/avif','audio/ogg','video/mp4','application/pdf']) {
 test(`model input, tool and assistant retain ${mime}`,()=>{
  const data=Buffer.alloc(32_000,42).toString('base64');
  const media={type:'media',source:{type:'base64',media_type:mime,data}};
  const user={role:'user',content:[{type:'text',text:'inspect'},media]};
  const tool={role:'toolResult',toolCallId:'call',content:[media]};
  const assistant={role:'assistant',content:[media]};
  const attrs:Record<string,unknown>={};
  const span={setAttribute:(k:string,v:unknown)=> {attrs[k]=v;}} as Span;
  setModelCallContent(span,assistant,{conversationMessages:[user,tool,assistant]});
  for(const direction of ['input','output']) assert.ok(String(attrs[`gen_ai.${direction}.messages`]).includes(data));
  setModelCallContent(span,assistant,{conversationMessages:[user,tool,assistant],captureInput:false,captureOutput:false});
  assert.equal(attrs['gen_ai.input.messages'],'[REDACTED]');
  assert.equal(attrs['gen_ai.output.messages'],'[REDACTED]');
  const content=[{type:'text',text:'x'.repeat(200)},media];
  assert.deepEqual(softTruncateMessages([{role:'user',content},{role:'assistant',content:''}],100,0)[0].content,content);
 });
}
