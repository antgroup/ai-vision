import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import {
  QueryClient,
  QueryClientProvider,
  QueryCache,
} from "@tanstack/react-query"
import { ApiAuthRedirectError } from "@/lib/api"

import "./index.css"
import "@/i18n"
import App from "./App.tsx"
import { ThemeProvider } from "@/components/layout/theme-provider"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import { migrateLocalStorage } from "@/lib/storageMigration"
import {
  SourceSystemProvider,
  DEFAULT_SOURCE_SYSTEM,
} from "@/contexts/SourceSystemContext"

migrateLocalStorage()

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      if (error instanceof ApiAuthRedirectError) return
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SourceSystemProvider sourceSystem={DEFAULT_SOURCE_SYSTEM}>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <TooltipProvider>
            <App />
            <Toaster position="bottom-right" />
          </TooltipProvider>
        </ThemeProvider>
      </SourceSystemProvider>
    </QueryClientProvider>
  </StrictMode>
)
