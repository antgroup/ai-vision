import test from 'node:test';
import assert from 'node:assert/strict';
import { mediaBlock, messageContent, MAX_MEDIA_BYTES } from './media-content.mjs';

for (const mime of ['image/png', 'image/avif', 'audio/wav', 'audio/ogg', 'video/mp4', 'video/ogg', 'application/pdf', 'text/markdown']) {
  test(`preserves explicit ${mime} bytes across common Agent formats`, () => {
    const data = Buffer.from('media contract bytes').toString('base64');
    for (const block of [
      { type: 'media', source: { type: 'base64', media_type: mime, data } },
      { type: 'file', mime, url: `data:${mime};base64,${data}` },
      { type: 'resource', resource: { mimeType: mime, blob: data } },
    ]) {
      const projected = mediaBlock(block);
      assert.equal(projected.source.media_type, mime);
      assert.equal(projected.source.data, data);
      assert.deepEqual(mediaBlock(projected), projected);
    }
  });
}
test('retains mixed order, leaves unknown data and local paths unresolved', () => {
  const media = { type: 'input_audio', input_audio: { format: 'mp3', data: 'YWJj' } };
  const parts = messageContent([{type:'text',text:'a'}, media, {type:'text',text:'b'}], '', s => s.toUpperCase());
  assert.deepEqual(parts.map(p => p.type), ['text','audio','text']);
  assert.equal(parts[0].text,'A');
  assert.equal(mediaBlock({data:'YWJj'}),undefined);
  assert.equal(mediaBlock({type:'file',mime:'audio/wav',url:'/tmp/audio.wav'}),undefined);
  assert.equal(mediaBlock({type:'file',mime:'text/html',data:'YWJj'}),undefined);
  assert.equal(messageContent([{type:'text',text:'hello'}], 'hello'),'hello');
});
test('validates syntax, MIME consistency and exact decoded 16 MiB boundary', () => {
  const data = Buffer.alloc(MAX_MEDIA_BYTES).toString('base64');
  assert.equal(mediaBlock({type:'audio',mime_type:'audio/wav',data}).source.data.length,data.length);
  assert.equal(mediaBlock({type:'audio',mime_type:'audio/wav',data:Buffer.alloc(MAX_MEDIA_BYTES+1).toString('base64')}).reason,'budget');
  for (const bad of ['a','ab=c','====','YWJj\n']) assert.equal(mediaBlock({type:'audio',mime_type:'audio/wav',data:bad}).reason,'invalid');
  assert.equal(mediaBlock({type:'file',mime:'audio/wav',url:'data:video/mp4;base64,YWJj'}).reason,'invalid');
});
test('does not fetch remote URLs and keeps their media type', () => {
  for (const type of ['image_url','audio_url','video_url']) {
    const url='https://example.invalid/media';
    assert.equal(mediaBlock({type,[type]:{url}})[type].url,url);
  }
});

test('media errors survive repeated normalization', () => {
  for (const reason of ['invalid', 'budget']) {
    const error = { type: 'aivision_media_error', reason };
    assert.deepEqual(mediaBlock(error), error);
    assert.deepEqual(messageContent([error]), [error]);
  }
});

test('both message directions enforce every media type at the exact file boundary', () => {
  const exact = Buffer.alloc(MAX_MEDIA_BYTES).toString('base64');
  const oversized = Buffer.alloc(MAX_MEDIA_BYTES + 1).toString('base64');
  for (const role of ['user', 'assistant']) {
    for (const mime of ['image/png', 'audio/wav', 'video/webm', 'application/pdf']) {
      const project = data => messageContent([
        { type: 'text', text: `${role} caption` },
        { type: 'media', mime_type: mime, data },
        { type: 'text', text: 'keep the following text' },
      ]);
      const valid = project(exact);
      assert.equal(valid[1].source.data, exact);
      assert.equal(valid[1].source.media_type, mime);
      const bounded = project(oversized);
      assert.deepEqual(bounded[1], { type: 'aivision_media_error', reason: 'budget' });
      assert.equal(bounded[0].text, `${role} caption`);
      assert.equal(bounded[2].text, 'keep the following text');
    }
  }
});
