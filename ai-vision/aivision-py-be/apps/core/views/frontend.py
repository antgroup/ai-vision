"""前端 SPA 入口与静态资源代理：把 CDN 上的内容通过后端域返回，保持同源。

部署模型：
- 前端由 Vite 构建，产物可发布到配置的静态资源服务。
- 后端通过 FRONTEND_CDN_BASE 环境变量决定从哪个 CDN 拉取 index.html 和 /assets/*。
- 浏览器始终访问后端域名 → SPA 与 API 同源 → 无需 CORS / withCredentials / Cookie SameSite=None。

Dev 模式 (FRONTEND_CDN_BASE 留空)：
- 后端 SPA / assets 路径返回 503。开发用 vite dev server（cd vfe && npm run dev），
  vite 在 :5174 + 代理 /api 到 :8000。
"""

import logging

import requests
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.views import View

logger = logging.getLogger(__name__)


# 资源请求头透传（支持 304 缓存校验、Range 请求、编码协商）
_PROXY_REQ_HEADERS = ("range", "if-none-match", "if-modified-since", "accept-encoding")

# 资源响应头透传（保持 CDN 的缓存语义和编码协商）
_PROXY_RESP_HEADERS = (
    "content-type",
    "content-length",
    "cache-control",
    "etag",
    "last-modified",
    "expires",
    "vary",
    "content-encoding",
    "accept-ranges",
    "content-range",
)


def _503_not_configured() -> HttpResponse:
    return HttpResponse(
        "<h1>Service Unavailable</h1>"
        "<p>FRONTEND_CDN_BASE is not configured for this environment. "
        "Use vite dev server (cd vfe &amp;&amp; npm run dev) for local development.</p>",
        status=503,
        content_type="text/html; charset=utf-8",
    )


def _502_cdn_error(detail: str) -> HttpResponse:
    return HttpResponse(
        f"<h1>Bad Gateway</h1><p>{detail}</p>",
        status=502,
        content_type="text/html; charset=utf-8",
    )


class FrontendIndexView(View):
    """SPA 入口：代理拉取 CDN 的 index.html 返回。

    所有非 /api/、/aiv-panel/、/static/、/assets/ 路径都命中本视图，
    返回同一份 CDN index.html，由前端 React Router 接管路由解析。

    透传 CDN 的 cache-control / etag / last-modified，并支持 304 协商缓存，
    避免每次刷页都重新拉取 index.html。
    """

    def get(self, request, *args, **kwargs) -> HttpResponse:
        cdn_base = settings.FRONTEND_CDN_BASE
        if not cdn_base:
            return _503_not_configured()

        url = f"{cdn_base}/index.html"
        fwd_headers = {
            h: request.headers[h]
            for h in _PROXY_REQ_HEADERS
            if h in request.headers
        }

        try:
            upstream = requests.get(
                url,
                headers=fwd_headers,
                stream=True,
                timeout=(settings.FRONTEND_CDN_TIMEOUT, 30),
            )
        except requests.RequestException as exc:
            logger.error("Frontend CDN unreachable: %s url=%s", exc, url)
            return _502_cdn_error(f"CDN unreachable: {exc}")

        if upstream.status_code not in (200, 304):
            logger.error(
                "Frontend CDN non-200: status=%s url=%s body=%s",
                upstream.status_code,
                url,
                upstream.text[:200],
            )
            return _502_cdn_error(f"CDN returned {upstream.status_code}")

        if upstream.status_code == 304:
            resp: HttpResponse = HttpResponse(status=304)
        else:
            # 用原始字节流转发，避免 requests 自动解压导致 content-encoding 头不一致
            resp = StreamingHttpResponse(
                upstream.raw.stream(8192, decode_content=False),
                status=upstream.status_code,
            )

        for h in _PROXY_RESP_HEADERS:
            v = upstream.headers.get(h)
            if v:
                resp[h] = v

        return resp


class CdnAssetProxyView(View):
    """/assets/<path> 应用层代理到 CDN。

    - 浏览器完全同源，不依赖 CDN 的 CORS 配置
    - 流式响应，避免大文件占用 worker 内存
    - 透传 Content-Type / Cache-Control / ETag / Content-Encoding 等头
    - 支持 304 协商缓存（透传 If-None-Match / If-Modified-Since）
    - 资源用 Vite hash 文件名 + immutable，单文件浏览器只回源拉一次
    """

    def get(self, request, asset_path, *args, **kwargs) -> HttpResponse:
        cdn_base = settings.FRONTEND_CDN_BASE
        if not cdn_base:
            return _503_not_configured()

        target = f"{cdn_base}/assets/{asset_path}"
        fwd_headers = {
            h: request.headers[h]
            for h in _PROXY_REQ_HEADERS
            if h in request.headers
        }

        try:
            upstream = requests.get(
                target,
                headers=fwd_headers,
                stream=True,
                timeout=(settings.FRONTEND_CDN_TIMEOUT, 30),  # (connect, read)
            )
        except requests.RequestException as exc:
            logger.error("CDN asset unreachable: %s url=%s", exc, target)
            return HttpResponse(status=502)

        # 204 / 304 无 body
        if upstream.status_code in (204, 304):
            resp: HttpResponse = HttpResponse(status=upstream.status_code)
        else:
            # 用原始字节流转发，避免 requests 自动解压导致 content-encoding/length 与 body 不一致
            # （`iter_content` 会按 Content-Encoding 自动解压；浏览器拿到的仍带 content-encoding: gzip
            #   就会再解一次 → ERR_CONTENT_DECODING_FAILED）
            resp = StreamingHttpResponse(
                upstream.raw.stream(8192, decode_content=False),
                status=upstream.status_code,
            )

        for h in _PROXY_RESP_HEADERS:
            v = upstream.headers.get(h)
            if v:
                resp[h] = v

        return resp
