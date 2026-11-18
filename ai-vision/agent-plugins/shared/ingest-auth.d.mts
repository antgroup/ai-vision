export declare function ingestHeaders(filename: string | undefined, endpoint: string, fallback?: Record<string, string>): Record<string, string> | (() => Promise<Record<string, string>>);
