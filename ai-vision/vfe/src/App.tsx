import { RouterProvider } from "react-router-dom"

import { useThemeToggle } from "@/hooks/use-theme-toggle"
import { router } from "@/router"

export function App() {
  useThemeToggle()

  return <RouterProvider router={router} />
}

export default App
