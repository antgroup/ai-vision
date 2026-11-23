import { createHash } from 'node:crypto';
import { closeSync, constants, fstatSync, lstatSync, openSync, readSync } from 'node:fs';
import type { DshContentBlock } from './types.js';
import { truncateText } from './privacy.js';
import { mediaBlock } from '../../../shared/media-content.mjs';

const MAX_BYTES = 16 * 1024 * 1024;
const NO_FOLLOW = constants.O_NOFOLLOW ?? 0;
const ATTACHMENT_OPEN_FLAGS = constants.O_RDONLY | NO_FOLLOW | (constants.O_NONBLOCK ?? 0);
export const MAX_MEDIA_TRANSPORT_CHARS = 48 * 1024 * 1024;

interface MediaBudget {
  remainingChars: number;
}

export interface DshImageReference {
  attachmentId: string;
  mediaType?: string;
  bytes: number;
  width?: number;
  height?: number;
  name?: string;
}

export type DshImageReader = (ref: DshImageReference) => Uint8Array;

/** Only the mounted host attachment provider may resolve a durable reference. */
export function hostImageReader(provider: { imageHostPath(ref: DshImageReference): string | undefined; fileHostPath?(ref: DshImageReference): string | undefined }): DshImageReader {
  return (ref) => {
    const path = ref.mediaType?.startsWith('image/') ? provider.imageHostPath(ref) : provider.fileHostPath?.(ref);
    if (!path) throw new Error('Attachment provider has no host mapping');
    // Windows lacks O_NOFOLLOW. Check the directory entry and compare its
    // file identity with the opened handle before reading any bytes.
    const expectedFile = NO_FOLLOW ? undefined : lstatSync(path, { bigint: true });
    if (expectedFile && (!expectedFile.isFile() || expectedFile.ino === 0n)) {
      throw new Error('Attachment path must identify a regular file');
    }
    const fd = openSync(path, ATTACHMENT_OPEN_FLAGS);
    try {
      const info = fstatSync(fd, { bigint: true });
      if (expectedFile && (info.dev !== expectedFile.dev || info.ino !== expectedFile.ino)) {
        throw new Error('Attachment file changed before it was opened');
      }
      const size = Number(info.size);
      if (!info.isFile() || size !== ref.bytes || size <= 0 || size > MAX_BYTES) throw new Error('Invalid attachment size');
      const data = Buffer.alloc(size);
      let offset = 0;
      while (offset < data.length) {
        const count = readSync(fd, data, offset, data.length - offset, null);
        if (!count) throw new Error('Incomplete attachment');
        offset += count;
      }
      return data;
    } finally {
      closeSync(fd);
    }
  };
}

/** Preserve message order; text-only messages keep their existing representation. */
export function mediaContent(blocks: DshContentBlock[], fallback: string, readImage?: DshImageReader, formatText?: (text: string) => string, budget: MediaBudget = { remainingChars: MAX_MEDIA_TRANSPORT_CHARS }): string | Record<string, unknown>[] {
  const parts: Record<string, unknown>[] = [];
  let found = false;
  let textRemaining = 65536;
  let visited = 0;
  const addMedia = (media: Record<string, unknown>): void => {
    const source = media.source as Record<string, unknown> | undefined;
    const chars = typeof source?.data === 'string' ? source.data.length : 0;
    if (chars > budget.remainingChars) parts.push({ type: 'aivision_media_error', reason: 'budget' });
    else {
      budget.remainingChars -= chars;
      parts.push(media);
    }
  };
  const visit = (items: unknown[], depth: number): void => {
    if (depth > 16) return;
    for (const item of items) {
      if (++visited > 100) { found = true; if (visited === 101) parts.push({ type: 'aivision_media_error', reason: 'budget' }); return; }
      if (!item || typeof item !== 'object') continue;
      const block = item as DshContentBlock;
      if (['image', 'file', 'audio', 'video', 'document'].includes(block.type) && block.attachment) {
        found = true;
        const ref = block.attachment as DshImageReference;
        let reason = 'invalid';
        try {
          if (!/^sha256:[0-9a-f]{64}$/.test(ref.attachmentId) || !Number.isSafeInteger(ref.bytes) || ref.bytes <= 0) throw new Error();
          if (ref.bytes > MAX_BYTES) { reason = 'budget'; throw new Error(); }
          if (4 * Math.ceil(ref.bytes / 3) > budget.remainingChars) { reason = 'budget'; throw new Error(); }
          if (!readImage) throw new Error();
          const data = readImage(ref);
          if (data.byteLength !== ref.bytes || createHash('sha256').update(data).digest('hex') !== ref.attachmentId.slice(7)) throw new Error();
          const mime = ref.mediaType ?? fileMime(Buffer.from(data), ref.name);
          if (!mime) throw new Error();
          const projected = mediaBlock({ type: 'media', mime_type: mime, data: Buffer.from(data).toString('base64') });
          if (!projected) throw new Error();
          addMedia(projected);
          continue;
        } catch {
          parts.push({ type: 'aivision_media_error', reason });
        }
      } else {
        const media = mediaBlock(block);
        if (media) { found = true; addMedia(media); }
        else if (block.type === 'text' && typeof block.text === 'string') parts.push({ type: 'text', text: block.text });
        else if (Array.isArray(block.content)) visit(block.content, depth + 1);
      }
    }
  };
  visit(blocks, 0);
  if (!found) return fallback;
  return parts.map(part => {
    if (part.type !== 'text') return part;
    const value = String(part.text);
    const bytes = Buffer.byteLength(value, 'utf8');
    const text = formatText ? formatText(value) : textRemaining > 0 ? truncateText(value, textRemaining)
      : bytes > 0 ? `...[truncated:${bytes} bytes]` : '';
    textRemaining = Math.max(0, textRemaining - bytes);
    return { ...part, text };
  });
}

// FileAttachmentRef has no MIME field. Recognize binary signatures, never paths.
function fileMime(data: Buffer, name?: string): string | undefined {
  if (data.subarray(0, 5).toString() === '%PDF-') return 'application/pdf';
  if (data.subarray(0, 4).toString() === 'OggS') return 'audio/ogg';
  if (data.subarray(0, 4).toString() === 'fLaC') return 'audio/flac';
  if (data.subarray(0, 3).toString() === 'ID3' || (data[0] === 255 && ((data[1] ?? 0) & 224) === 224)) return 'audio/mpeg';
  if (data.subarray(0, 4).toString() === 'RIFF') {
    if (data.subarray(8, 12).toString() === 'WAVE') return 'audio/wav';
    if (data.subarray(8, 12).toString() === 'WEBP') return 'image/webp';
    if (data.subarray(8, 12).toString() === 'AVI ') return 'video/x-msvideo';
  }
  if (data.subarray(4, 8).toString() === 'ftyp') return data.subarray(8, 12).toString().startsWith('avif') ? 'image/avif' : 'video/mp4';
  if (data.subarray(0, 4).equals(Buffer.from([26, 69, 223, 163]))) return 'video/webm';
  if (data.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return 'image/png';
  if (data[0] === 255 && data[1] === 216 && data[2] === 255) return 'image/jpeg';
  if (data.subarray(0, 3).toString() === 'GIF') return 'image/gif';
  if (/\.(?:txt|md|markdown)$/i.test(name ?? '')) {
    try { new TextDecoder('utf-8', { fatal: true }).decode(data); return /\.(?:md|markdown)$/i.test(name!) ? 'text/markdown' : 'text/plain'; } catch { return undefined; }
  }
  return undefined;
}
