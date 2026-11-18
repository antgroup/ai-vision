export const MAX_MEDIA_BYTES: number;
export function mediaBlock(value: unknown): Record<string, unknown> | undefined;
export function messageContent(content: unknown, fallback?: string, formatText?: (value: string) => string): string | Record<string, unknown>[];
