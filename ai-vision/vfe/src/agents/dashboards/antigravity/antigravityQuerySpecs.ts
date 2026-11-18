/**
 * Antigravity query specs — generated from field config via buildOtelQuerySpecs.
 */

import { buildOtelQuerySpecs } from "@/agents/dashboards/otel-agent/queries/buildQuerySpecs"
import { antigravityConfig } from "./antigravityConfig"

export const antigravityQuerySpecs = buildOtelQuerySpecs(antigravityConfig)
