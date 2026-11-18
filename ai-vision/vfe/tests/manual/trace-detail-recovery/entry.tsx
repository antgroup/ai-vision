import { StrictMode, useState } from "react"
import { MemoryRouter } from "react-router-dom"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { NativeTraceDetail } from "@/components/traces-otel/NativeTraceDetail"
import type { NativeTraceDetailResponse } from "@/components/traces-otel/types"
import { TooltipProvider } from "@/components/ui/tooltip"
import { IOSection } from "@/components/traces-otel/shared/MetadataTree"
import i18n from "@/i18n"
import "@/index.css"
import { fault } from "./fault"

const params = new URLSearchParams(location.search)
await i18n.changeLanguage(params.get("lang") || "zh-CN")
document.documentElement.classList.toggle(
  "dark",
  params.get("theme") === "dark"
)
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})
const cases: [string, unknown][] = [
  ["未记录内容 / undefined", undefined],
  ["显式 null", null],
  ['空字符串 / ""', ""],
  ["false", false],
  ["0", 0],
  ["空数组 / []", []],
  ["空对象 / {}", {}],
]

function trace(id: string): NativeTraceDetailResponse {
  const names = [
    ["root", "容错验收 · 受控测试数据"],
    ["fault", "模拟异常的调用"],
    ["healthy", "正常调用 · 可继续查看"],
    ["absent", "未记录输入输出的调用"],
    ["input-only", "只有输入的调用"],
    ["output-only", "只有输出的调用"],
    ["explicit-empty", "显式空值的调用"],
  ]
  return {
    traceId: id,
    spans: names.map(([spanId, name], i) => ({
      trace_id: id,
      span_id: spanId,
      parent_span_id: i ? "root" : undefined,
      name,
      status: { code: "OK" },
      start_time_unix_nano: String(
        1792627200000000000n + BigInt(i) * 1000000000n
      ),
      end_time_unix_nano: String(
        1792627200000000000n + BigInt(i ? i + 1 : names.length) * 1000000000n
      ),
      attributes:
        spanId === "absent"
          ? { "custom.input": "合成数据：未识别的字段示例" }
          : spanId === "input-only"
            ? { "gen_ai.input.messages": "等待结果的测试输入" }
            : spanId === "output-only"
              ? { "gen_ai.output.messages": "仅有结果的测试输出" }
              : spanId === "explicit-empty"
                ? { "gen_ai.input.messages": null, "gen_ai.output.messages": 0 }
                : {
                    "gen_ai.span.kind": i ? "TOOL" : "CHAIN",
                    "langfuse.observation.input": JSON.stringify({
                      query: "受控样本，不调用真实模型",
                    }),
                    "langfuse.observation.output": JSON.stringify({
                      ok: true,
                      trace: id,
                      items: 0,
                    }),
                  },
    })),
    semantics: {
      rootObservationId: "root",
      missingParentIds: [],
      rootSelectionReason: "trace_root:explicit",
      input: "局部异常隔离测试",
      output: "合成数据",
      outputState: "available",
    },
  }
}

export function Acceptance() {
  const [opened, setOpened] = useState(false)
  const [traceId, setTraceId] = useState("controlled-trace-a")
  const [mode, setMode] = useState<"formatted" | "json">("json")
  return (
    <main
      className="min-h-screen bg-background p-8 text-foreground"
      onClickCapture={(event) => {
        const button = (event.target as HTMLElement).closest("button")
        // Simulate a dependency recovering before retry. Persistent mode stays broken.
        if (
          button?.textContent === i18n.t("traceLabels.retryDetail") &&
          params.get("persistent") !== "1"
        )
          fault.armed = false
      }}
    >
      <h1 className="text-xl font-semibold">#100 · Trace 详情容错验收</h1>
      <p className="my-3 text-sm text-muted-foreground">
        真实前端组件 + 合成 Trace；异常仅由测试入口注入，不是线上故障复现。
      </p>
      <div className="mb-6 flex gap-3">
        <button
          type="button"
          className="rounded border px-4 py-2"
          onClick={() => {
            fault.armed = true
            setOpened(true)
          }}
        >
          打开 Trace（注入异常）
        </button>
        <button
          type="button"
          className="rounded border px-4 py-2"
          onClick={() =>
            setTraceId(
              traceId.endsWith("a")
                ? "controlled-trace-b"
                : "controlled-trace-a"
            )
          }
        >
          切换测试 Trace
        </button>
        <span className="self-center text-sm">{traceId}</span>
        <button
          type="button"
          className="rounded border px-4 py-2"
          onClick={() => setMode(mode === "json" ? "formatted" : "json")}
        >
          IO 模式：{mode}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {cases.map(([title, value]) => (
          <section key={title} className="rounded-lg border p-4">
            <IOSection
              title={title}
              data={value}
              variant="output"
              viewMode={mode}
              preserveValue
              valuePresent={value !== undefined}
            />
          </section>
        ))}
      </div>
      <NativeTraceDetail
        spaceId="synthetic-space"
        traceId={opened ? traceId : null}
        traceData={trace(traceId)}
        labsApi={false}
        showHeaderActions={false}
        onClose={() => setOpened(false)}
      />
    </main>
  )
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter>
          <Acceptance />
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>
)
