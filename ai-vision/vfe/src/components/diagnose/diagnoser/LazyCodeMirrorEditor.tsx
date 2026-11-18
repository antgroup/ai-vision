import { lazy, Suspense, type ComponentProps } from "react"
import { Loader2 } from "lucide-react"

// codemirror 是纯 ESM 包，Vite 预构建时 interop 不稳定，会让主模块图加载失败。
// 把 CodeMirrorEditor 拆成懒加载，让 codemirror 只在诊断器弹窗用到时才动态引入，
// 主应用（dashboard、trace 等页面）永不静态依赖 codemirror。
const CodeMirrorEditor = lazy(() =>
  import("./CodeMirrorEditor").then((m) => ({ default: m.CodeMirrorEditor }))
)

type CodeMirrorEditorProps = ComponentProps<typeof CodeMirrorEditor>

export function LazyCodeMirrorEditor(props: CodeMirrorEditorProps) {
  return (
    <Suspense
      fallback={
        <div className="flex h-[300px] w-full items-center justify-center rounded-md border text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
        </div>
      }
    >
      <CodeMirrorEditor {...props} />
    </Suspense>
  )
}
