export interface ClaudeResponse {
  result: string;
  session_id: string;
  total_cost_usd: number;
  usage: {
    input_tokens: number;
    output_tokens: number;
  };
  structured_output?: unknown;
}

export interface AnalysisResult {
  summary: string;
  key_points: string[];
}

export const analysisSchema = {
  type: "object" as const,
  properties: {
    summary: { type: "string" as const },
    key_points: { type: "array" as const, items: { type: "string" as const } },
  },
  required: ["summary", "key_points"],
};
