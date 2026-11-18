#!/usr/bin/env tsx
import assert from 'node:assert/strict';
import { createProviderWireObserver } from '../src/hooks/provider-wire.js';
const failures: string[]=[];
async function run(name:string,fn:()=>Promise<void>){try{await fn();}catch(e){failures.push(`${name}: ${e}`);}}
const encoded=(value:string)=>new TextEncoder().encode(value);
const stream=(parts:Uint8Array[],onCancel?:(reason:unknown)=>void)=>new ReadableStream<Uint8Array>({pull(c){const p=parts.shift();if(p)c.enqueue(p);else c.close();},cancel(r){onCancel?.(r);}});
const identity={sessionID:'wire-session',agent:'build',message:{id:'user-real'},model:{id:'model-real',providerID:'local',api:{npm:'@ai-sdk/openai-compatible'}}};
function observer(fetcher:typeof fetch){
 const publications:any[]=[];
 const hooks=createProviderWireObserver({correlate:(input:any)=>({...input,assistantMessageId:'assistant-real'}),publish:(result:any)=>publications.push(result)});
 const config:any={provider:{local:{npm:'@ai-sdk/openai-compatible',options:{fetch:fetcher}}}};hooks.config(config);
 const output={headers:{'existing-header':'original'}};hooks.headers(identity,output);
 return {hooks,config,headers:output.headers,publications};
}
async function invoke(observed:ReturnType<typeof observer>,signal?:AbortSignal){return observed.config.provider.local.options.fetch('http://127.0.0.1/v1/chat/completions',{method:'POST',headers:observed.headers,signal,body:JSON.stringify({model:'model-real',stream:true})});}
await run('split SSE preserves bytes and reports only actual final usage',async()=>{
 const raw='data: {"choices":[{"delta":{"content":"private model text"}}]}\r\n\r\ndata: {"choices":[],"usage":{"prompt_tokens":16,"completion_tokens":4,"total_tokens":20,"prompt_tokens_details":{"cached_tokens":3},"completion_tokens_details":{"reasoning_tokens":1}}}\n\ndata: [DONE]\n\n';
 const pieces=[raw.slice(0,61),raw.slice(61,179),raw.slice(179)].map(encoded);const controller=new AbortController();let seen:any;
 const observed=observer((async(input,init)=>{seen={input,init};return new Response(stream(pieces),{headers:{'content-type':'text/event-stream','x-test':'same'}});}) as typeof fetch);
 const response=await invoke(observed,controller.signal);assert.equal(await response.text(),raw);assert.equal(response.headers.get('x-test'),'same');assert.equal(seen.init.signal,controller.signal);assert.equal(new Headers(seen.init.headers).get('existing-header'),'original');assert.equal([...new Headers(seen.init.headers).keys()].some(k=>k.startsWith('x-observ-opencode')),false);
 assert.equal(observed.publications.length,1);assert.deepEqual(observed.publications[0].usage,{input:16,output:4,total:20,cacheRead:3,reasoning:1});assert.equal(observed.publications[0].identity.assistantMessageId,'assistant-real');assert.equal(JSON.stringify(observed.publications).includes('private model text'),false);
});
await run('unknown and explicit zero remain distinct',async()=>{
 for(const usage of [undefined,{prompt_tokens:0,completion_tokens:0,total_tokens:0}]){const raw='data: '+JSON.stringify({choices:[],...(usage?{usage}:{})})+'\n\ndata: [DONE]\n\n';const observed=observer((async()=>new Response(stream([encoded(raw)]),{headers:{'content-type':'text/event-stream'}})) as typeof fetch);await (await invoke(observed)).text();assert.deepEqual(observed.publications[0].usage,usage?{input:0,output:0,total:0}:{});}
});
await run('nonstream and partial usage do not manufacture totals',async()=>{const observed=observer((async()=>new Response(JSON.stringify({choices:[],usage:{prompt_tokens:7}}),{headers:{'content-type':'application/json'}})) as typeof fetch);await (await invoke(observed)).json();assert.deepEqual(observed.publications[0].usage,{input:7});});
await run('oversized line drops capture without changing transport',async()=>{const raw='data: '+JSON.stringify({choices:[{delta:{content:'x'.repeat(200000)}}]})+'\n\ndata: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\ndata: [DONE]\n\n';const observed=observer((async()=>new Response(stream([encoded(raw)]),{headers:{'content-type':'text/event-stream'}})) as typeof fetch);assert.equal(await (await invoke(observed)).text(),raw);assert.deepEqual(observed.publications[0].usage,{input:2,output:1,total:3});});
await run('reader cancellation reaches original stream once',async()=>{let cancelled:any;const observed=observer((async()=>new Response(stream(Array.from({length:10},()=>encoded('data: {"choices":[]}\n\n')),r=>cancelled=r),{headers:{'content-type':'text/event-stream'}})) as typeof fetch);const reader=(await invoke(observed)).body!.getReader();await reader.read();await reader.cancel('actual cancel');assert.equal(cancelled,'actual cancel');assert.equal(observed.publications.length,1);assert.equal(observed.publications[0].errorType,'AbortError');});
await run('request failure preserves error identity and bounded facts',async()=>{const failure=new Error('private-error-text');const observed=observer((async()=>{throw failure;}) as typeof fetch);await assert.rejects(invoke(observed),e=>e===failure);assert.equal(observed.publications[0].errorType,'Error');assert.equal(JSON.stringify(observed.publications).includes('private-error-text'),false);});
await run('unrelated providers and endpoints are unchanged',async()=>{let responses=0;const fetcher=(async()=>{responses++;return new Response('{}');}) as typeof fetch;const observed=observer(fetcher);const cfg:any={provider:{anthropic:{npm:'@ai-sdk/anthropic',options:{fetch:fetcher}}}};observed.hooks.config(cfg);assert.equal(cfg.provider.anthropic.options.fetch,fetcher);await observed.config.provider.local.options.fetch('http://127.0.0.1/v1/models',{});assert.equal(responses,1);assert.equal(observed.publications.length,0);});
await run('actual AbortSignal ends a blocked reader without inventing usage',async()=>{
 const controller=new AbortController();let originalSignal:AbortSignal|undefined;
 const observed=observer((async(_input,init)=>{originalSignal=init?.signal as AbortSignal;return new Response(new ReadableStream<Uint8Array>({pull(){return new Promise<void>(()=>{});}}),{headers:{'content-type':'text/event-stream'}});}) as typeof fetch);
 const response=await invoke(observed,controller.signal);controller.abort();
 assert.equal(originalSignal,controller.signal);assert.equal(observed.publications.length,1);assert.equal(observed.publications[0].errorType,'AbortError');assert.deepEqual(observed.publications[0].usage,{});
 // The observer must not replace or consume the original blocked transport.
 assert.ok(response.body);controller.abort();assert.equal(observed.publications.length,1);
});
if(failures.length){for(const f of failures)console.error('FAIL '+f);process.exitCode=1;}else console.log('PASS 8 provider wire usage cases');
