/**
 * Kimi CLI query specs — generated from field config via buildOtelQuerySpecs.
 */

import { buildOtelQuerySpecs } from "@/agents/dashboards/otel-agent/queries/buildQuerySpecs"
import { kimiCliConfig } from "./kimiCliConfig"

export const kimiCliQuerySpecs = buildOtelQuerySpecs(kimiCliConfig)
