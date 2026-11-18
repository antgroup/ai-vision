import test from 'node:test';
import assert from 'node:assert/strict';
import { CanonicalNormalizer } from '../src/normalize.js';
const ctx={sessionManager:{getSessionId:()=> 'media-contract'}};
test('provider nested media retains full data while ordinary strings remain bounded',()=>{
 const data=Buffer.alloc(32_000,42).toString('base64');
 const n=new CanonicalNormalizer();
 const e=n.normalize('before_provider_request',{payload:{messages:[{role:'user',content:[{type:'image',mimeType:'image/png',data},{type:'text',text:'x'.repeat(30_000)}]}]}},ctx);
 const json=JSON.stringify(e);
 assert.ok(json.includes(data));
 assert.ok(!json.includes('x'.repeat(30_000)));
 assert.ok(e.projection.truncated);
});

test('media URLs obey secret scrubbing in direct arrays and nested messages', () => {
 const secret = 'sk-' + 's'.repeat(24);
 for (const [hook, payload] of [
  ['before_agent_start', { images: [{ type: 'image_url', image_url: { url: `https://example.invalid/image?token=${secret}` } }] }],
  ['before_provider_request', { payload: { messages: [{ role: 'user', content: [{ type: 'image_url', image_url: { url: `https://example.invalid/image?token=${secret}` } }] }] } }],
 ] as const) {
  const event = new CanonicalNormalizer().normalize(hook, payload, ctx);
  assert.ok(!JSON.stringify(event).includes(secret));
  assert.ok(event.projection.redacted);
 }
});
