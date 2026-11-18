import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes"

type ThemeProviderProps = React.ComponentProps<typeof NextThemesProvider>

function ThemeProvider({ children, ...props }: ThemeProviderProps & { children: React.ReactNode }) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>
}

// eslint-disable-next-line react-refresh/only-export-components
export { ThemeProvider, useTheme }