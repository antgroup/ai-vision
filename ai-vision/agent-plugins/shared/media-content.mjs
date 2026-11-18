/** Explicit media transport for telemetry. Never fetch URLs or open local paths.
 * SigNoz validates signatures, persists bytes and replaces these blocks with refs.
 */
export const MAX_MEDIA_BYTES = 16 * 1024 * 1024;
const TYPES = new Set(['image', 'audio', 'video', 'document', 'file', 'media', 'base64', 'image_url', 'input_image', 'audio_url', 'video_url', 'input_audio', 'input_file', 'resource', 'text']);
const MIME = /^(?:image\/[a-z0-9.+-]+|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+|application\/pdf|text\/(?:plain|markdown|x-markdown))$/i;
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};

/** Undefined means this is not an explicit supported media descriptor. */
export function mediaBlock(value) {
  const block = object(value);
  if (block.type === 'aivision_media_error' && ['invalid', 'budget'].includes(block.reason)) return { type: block.type, reason: block.reason };
  if (!TYPES.has(block.type)) return undefined;
  let source = object(block.source);
  if (!Object.keys(source).length) source = block.type === 'resource' ? object(block.resource) : block;
  let mime = source.media_type ?? source.mime_type ?? source.mimeType ?? source.mimetype ?? block.mime;
  let data = source.data ?? source.blob ?? source.file_data;
  let url = source.url ?? source.file_url;
  for (const field of ['image_url', 'audio_url', 'video_url']) {
    const candidate = block[field];
    if (typeof candidate === 'string') url = candidate;
    else if (candidate && typeof candidate.url === 'string') url = candidate.url;
  }
  if (block.type === 'input_audio') {
    source = object(block.input_audio);
    mime = ({ wav: 'audio/wav', mp3: 'audio/mpeg', ogg: 'audio/ogg', flac: 'audio/flac' })[source.format];
    data = source.data;
  }
  if (block.type === 'input_file' || block.type === 'file') {
    source = Object.keys(object(block.file)).length ? block.file : source;
    data = source.file_data ?? data;
    url = source.file_url ?? url;
  }
  if (typeof data === 'string' && data.startsWith('data:')) url = data;
  if (typeof url === 'string' && url.startsWith('data:')) {
    const comma = url.indexOf(',');
    const header = url.slice(5, comma);
    if (comma < 0 || !header.endsWith(';base64')) return { type: 'aivision_media_error', reason: 'invalid' };
    const declared = header.slice(0, -7).toLowerCase();
    if (mime && String(mime).toLowerCase() !== declared) return { type: 'aivision_media_error', reason: 'invalid' };
    mime = declared;
    data = url.slice(comma + 1);
    url = undefined;
  }
  if (typeof data === 'string' && typeof mime === 'string' && MIME.test(mime)) {
    if (data.length > 4 * Math.ceil(MAX_MEDIA_BYTES / 3)) return { type: 'aivision_media_error', reason: 'budget' };
    // Strict syntax and decoded size; do not allocate a second copy of a large file.
    if (!data.length || data.length % 4 || /[^A-Za-z0-9+/=]/.test(data) || !/^[^=]*={0,2}$/.test(data)) return { type: 'aivision_media_error', reason: 'invalid' };
    const bytes = data.length / 4 * 3 - (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0);
    if (bytes > MAX_MEDIA_BYTES) return { type: 'aivision_media_error', reason: 'budget' };
    mime = mime.toLowerCase();
    const kind = mime.startsWith('image/') ? 'image' : mime.startsWith('audio/') ? 'audio' : mime.startsWith('video/') ? 'video' : mime === 'application/pdf' ? 'document' : 'text';
    return { type: kind, source: { type: 'base64', media_type: mime.toLowerCase(), data } };
  }
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) {
    // Keep external addresses unresolved. Browser consent remains mandatory.
    if (['image_url', 'input_image'].includes(block.type)) return { type: 'image_url', image_url: { url } };
    if (block.type === 'audio_url') return { type: 'audio_url', audio_url: { url } };
    if (block.type === 'video_url') return { type: 'video_url', video_url: { url } };
    if (typeof mime === 'string' && MIME.test(mime)) return { type: 'media', mime_type: mime.toLowerCase(), url };
  }
  return undefined;
}

/** Preserve ordered content only when a recognized media block is present. */
export function messageContent(content, fallback = '', formatText = value => value) {
  if (!Array.isArray(content)) return fallback;
  let found = false;
  const parts = [];
  for (const item of content.slice(0, 100)) {
    const media = mediaBlock(item);
    if (media) { found = true; parts.push(media); }
    else if (item && ['text', 'input_text', 'output_text'].includes(item.type) && typeof item.text === 'string') parts.push({ type: 'text', text: formatText(item.text) });
  }
  if (!found) return fallback;
  if (content.length > 100) parts.push({ type: 'aivision_media_error', reason: 'budget' });
  return parts;
}
