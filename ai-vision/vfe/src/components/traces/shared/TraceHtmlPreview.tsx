import { useMemo } from "react"
import { traceHtmlRootAttributes } from "./traceDocuments"

const staticElements = new Set(
  "a abbr address article aside b bdi bdo blockquote br caption center cite code col colgroup dd del details dfn div dl dt em figcaption figure footer h1 h2 h3 h4 h5 h6 header hr i img ins kbd li main mark nav ol p pre q rp rt ruby s samp section small span strong style sub summary sup table tbody td th thead tfoot time tr u ul var wbr".split(
    " "
  )
)
const staticAttributes = new Set(
  "alt class colspan dir height id lang open reversed rowspan scope start style title type value width".split(
    " "
  )
)
const documentAttributes = new Set([
  "class",
  "dir",
  "id",
  "lang",
  "style",
  "title",
])
const embeddedImage = /^data:image\/(?:png|jpeg|gif|webp|avif);base64,/i

function safeDocumentAttributes(template: HTMLTemplateElement, attrs: string) {
  if (!attrs) return ""
  // Browser parsing decodes entities and serializes attribute values safely.
  // Keep the attribute parse in the inert template as well as the body parse.
  const holder = template.ownerDocument.createElement("template")
  holder.innerHTML = `<div${attrs}></div>`
  const element = holder.content.firstElementChild
  if (!element) return ""
  for (const attribute of Array.from(element.attributes)) {
    if (!documentAttributes.has(attribute.name)) {
      element.removeAttribute(attribute.name)
    }
  }
  return element.outerHTML.slice("<div".length, -"></div>".length)
}

function htmlPreview(source: string): string {
  // Template contents are inert, including while parsing resource elements.
  const template = document.createElement("template")
  template.innerHTML = source
  for (const element of template.content.querySelectorAll("*")) {
    if (
      element.namespaceURI !== "http://www.w3.org/1999/xhtml" ||
      !staticElements.has(element.localName)
    ) {
      element.remove()
      continue
    }
    if (
      element.localName === "img" &&
      !embeddedImage.test(element.getAttribute("src") ?? "")
    ) {
      element.replaceWith(element.getAttribute("alt") ?? "")
      continue
    }
    for (const attribute of Array.from(element.attributes)) {
      if (
        staticAttributes.has(attribute.name) ||
        (element.localName === "table" && attribute.name === "border")
      )
        continue
      if (element.localName === "img" && attribute.name === "src") continue
      element.removeAttribute(attribute.name)
    }
  }

  const policy =
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
  const attrs = traceHtmlRootAttributes(source)
  const htmlAttrs = safeDocumentAttributes(template, attrs?.html ?? "")
  const bodyAttrs = safeDocumentAttributes(template, attrs?.body ?? "")
  return `<!doctype html><html${htmlAttrs}><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:system-ui,sans-serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}pre{overflow:auto}</style></head><body${bodyAttrs}>${template.innerHTML}</body></html>`
}

export default function TraceHtmlPreview({
  source,
  className = "block h-full w-full bg-white",
}: {
  source: string
  className?: string
}) {
  const srcDoc = useMemo(() => htmlPreview(source), [source])
  return (
    <iframe
      className={className}
      title="HTML"
      sandbox=""
      referrerPolicy="no-referrer"
      srcDoc={srcDoc}
    />
  )
}
