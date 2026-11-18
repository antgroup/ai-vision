import { useTranslation } from "react-i18next"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

interface SpanAttributeTableProps {
  title: string
  data: Record<string, unknown>
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "-"
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean")
    return String(value)
  return JSON.stringify(value, null, 2)
}

export function SpanAttributeTable({ title, data }: SpanAttributeTableProps) {
  const { t } = useTranslation()
  const entries = Object.entries(data).filter(
    ([, v]) => v !== null && v !== undefined
  )
  if (entries.length === 0) return null

  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold text-foreground">{title}</h4>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[280px]">{t("traceLabels.key")}</TableHead>
            <TableHead>{t("traceLabels.value")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map(([key, value]) => (
            <TableRow key={key}>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {key}
              </TableCell>
              <TableCell className="max-w-[500px] font-mono text-xs break-all">
                {renderValue(value)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
