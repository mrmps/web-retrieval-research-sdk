# Web retrieval research SDK

Call the same public REST operations from Python, Codex, or Claude while keeping each provider's request and response intact. The SDK exposes Context, Exa, Firecrawl, and Parallel search plus known-URL retrieval. Query-aware highlights are requested through each provider's own payload. It does not score answers or make unlike outputs look equivalent.

This repo is a small companion to the [WebCode highlights benchmark](https://github.com/context-dot-dev/webcode-highlights-benchmark) (currently private; access required). Its live protocol is a starting point for matched URL/question experiments, not a claim that these APIs share the same semantics. Its native Codex open and Claude WebFetch runs remain host-tool baselines; this SDK does not call an undocumented Codex backend or impersonate Claude Code's internal WebFetch tool.

## Install and connect

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
export CONTEXT_API_KEY=...
export EXA_API_KEY=...
export FIRECRAWL_API_KEY=...
export PARALLEL_API_KEY=...
.venv/bin/web-research serve
```

Set only the keys for providers you use. Keys are read from the environment at call time, sent in provider headers, and excluded from returned traces. The native request body is recorded verbatim, so do not put secrets in it. The MCP server uses stdio, so the client launches `web-research serve` itself. Example Codex configuration (`~/.codex/config.toml`):

```toml
[mcp_servers.web-retrieval-research]
command = "/absolute/path/to/this/repo/.venv/bin/web-research"
args = ["serve"]
env_vars = ["CONTEXT_API_KEY", "EXA_API_KEY", "FIRECRAWL_API_KEY", "PARALLEL_API_KEY"]
```

Example Claude Code command:

```bash
claude mcp add web-retrieval-research -- /absolute/path/to/this/repo/.venv/bin/web-research serve
```

The eight MCP tools are `context_search`, `context_scrape`, `exa_search`, `exa_contents`, `firecrawl_search`, `firecrawl_scrape`, `parallel_search`, and `parallel_extract`. Each takes a `request` JSON object in the provider's native schema and returns the complete provider response inside a trace envelope. This server adds no retry, fallback, or output cap; a Codex or Claude client may still limit what it displays. Native provider validation and limits still apply. A non-2xx response is returned with `status_code`, response body, and `error`, not disguised as an empty result. A 2xx response can still contain per-URL or per-format errors: inspect the body.

## Python and CLI examples

```python
from web_retrieval_research import ResearchClient

with ResearchClient() as client:
    trace = client.call("context", "scrape", {
        "url": "https://example.com/guide",
        "formats": {"highlights": True, "markdown": True},
        "highlightsParams": {"query": "What are the installation steps?"},
        "maxAgeMs": 0,
    })
    print(trace.to_dict())
```

```bash
web-research call exa contents --json '{"urls":["https://example.com/guide"],"highlights":{"query":"installation steps"},"maxAgeHours":0}'
web-research call parallel search --file examples/parallel-search.json
```

The CLI exits nonzero on HTTP or transport error and still prints the trace. It rejects a missing key or unsupported operation before making a request. `WEB_RESEARCH_<PROVIDER>_BASE_URL` can point at a local fixture server or private API gateway; record that override in a real experiment's manifest.

For a matched known-URL highlights run, pass the same URL and question with these native bodies (replace the example values):

| Operation | Request body |
|---|---|
| `context_scrape` | `{"url":"https://example.com/guide","formats":{"highlights":true},"highlightsParams":{"query":"installation steps"},"maxAgeMs":0}` |
| `exa_contents` | `{"urls":["https://example.com/guide"],"highlights":{"query":"installation steps"},"maxAgeHours":0}` |
| `firecrawl_scrape` | `{"url":"https://example.com/guide","formats":[{"type":"highlights","query":"installation steps"}],"maxAge":0}` |
| `parallel_extract` | `{"urls":["https://example.com/guide"],"objective":"installation steps","advanced_settings":{"fetch_policy":{"max_age_seconds":0,"disable_cache_fallback":true}}}` |

The Parallel request is deliberately included as a **best-effort** zero-age probe: its documented minimum is 600 seconds. For discovery, use native search bodies: Context `{"query":"installation steps","numResults":10}`, Exa `{"query":"installation steps","type":"auto"}`, Firecrawl `{"query":"installation steps","limit":10}`, and Parallel `{"objective":"Find installation guidance","search_queries":["installation steps guide"]}`.

## Capability and comparison boundaries

| Provider | Discovery | Known URL / fetch | Query passages | Full page | Freshness control |
|---|---|---|---|---|---|
| Context | `search` | `scrape` | `formats.highlights` + `highlightsParams.query`; search can add `highlightsOptions` | `formats.markdown` | `maxAgeMs` on scrape; search Markdown has its own setting |
| Exa | `search` | `contents` | `highlights.query`; search can add `contents.highlights` | `text` | `maxAgeHours` on contents |
| Firecrawl | `search` | `scrape` | highlights format object; search supports highlights | `formats: ["markdown"]` | `maxAge` on scrape |
| Parallel | `search` | `extract` | `objective` yields excerpts | `advanced_settings.full_content` | `advanced_settings.fetch_policy` on extract |
| Codex native | native search/open | native open | no dedicated highlight contract | rendered open result, subject to tool output limits | no exposed force-refresh switch in the benchmark's native open path |
| Claude Code native | native search/WebFetch | WebFetch | prompt-dependent synthesis | no guaranteed raw full page | no exposed force-refresh switch in the benchmark's WebFetch path |

The first four rows are callable here. Search changes *which URLs* are selected; known-URL retrieval controls *what is extracted from a URL*. A search-plus-fetch run is two stages and should save both raw responses. Parallel excerpts, vendor highlights, full Markdown, and agent-rendered page text are different interventions. Compare the same URLs and questions, label the operation, preserve each provider's failures, and report output size and source availability separately from answer quality.

## TTL and cache gotchas

| Provider | Documented behavior | Experimental consequence |
|---|---|---|
| Context scrape | `maxAgeMs` is milliseconds; default **3 days**, `0` fetches fresh, maximum **1 year**. Outputs are cached independently, so one response can combine outputs from different visits. Custom headers, browser actions, and zero data retention bypass cache reads and writes. | Save `cache_metadata` and each requested format's `success`; HTTP 200 alone does not prove a usable result. A fresh highlight request does not prove the search index is fresh. [Scrape docs](https://docs.context.dev/api-reference/web-scraping/scrape), [Search docs](https://docs.context.dev/api-reference/web-scraping/search). |
| Exa contents | Omitted `maxAgeHours` uses cache when available; positive values bound content age in **hours**; `0` requests fresh fetch; `-1` only uses cached content. Fresh fetching is bounded by `livecrawlTimeout` (default **10 seconds**). Search has its own indexed-result behavior. | Log `statuses[].source` and per-URL status. Do not read `maxAgeHours: 0` as a guaranteed successful fetch or a cold-search query. Do not combine deprecated `livecrawl` with `maxAgeHours`. [Contents docs](https://exa.ai/docs/contents/quickstart), [Search docs](https://exa.ai/docs/reference/search). |
| Firecrawl scrape | `maxAge` is milliseconds and defaults to **2 days**; `0` requests a new scrape. `minAge` is cache-only and returns `SCRAPE_NO_CACHED_DATA` on miss. `lockdown` is also cache-only, with a **2-year** default `maxAge` and its own miss error. `storeInCache: false` prevents storing this scrape; actions or custom headers can force it false. | Keep the native request and metadata. A cached search result and a fresh scrape are different conditions; cache-only misses remain failures. [Scrape docs](https://docs.firecrawl.dev/api-reference/endpoint/scrape), [Search docs](https://docs.firecrawl.dev/api-reference/endpoint/search). |
| Parallel extract | Dynamic fetch policy is the default. `max_age_seconds` is seconds with a **600-second minimum**; a requested zero is clamped. `disable_cache_fallback: true` returns an error if live fetch fails; false may return older indexed content. | Never label this `max_age_seconds: 0` as a cold fetch. Retain `warnings`, source metadata, and per-URL errors. Search reads the index and is not a live-fetch guarantee. [Extract settings](https://docs.parallel.ai/extract/advanced-extract-settings), [Search quickstart](https://docs.parallel.ai/search/search-quickstart). |
| Codex and Claude native | The host tools used in the benchmark expose no caller TTL control. Their response limits, internal caching, and text transformation are not the REST providers' contracts. | Record host, tool, model/version, prompt/arguments, timestamp, and observed result. Do not pool native-tool latency with API latency as if the invocation boundary were identical. [Benchmark protocol](https://github.com/context-dot-dev/webcode-highlights-benchmark/blob/main/live_protocol.json). |

**TTL is an eligibility policy, not a content timestamp or a promise of a new crawl.** Server-side cache misses can still hit CDNs and site caches. `0` settings do not make search indexes, a target website, or the grader cold. Record returned cache fields rather than inferring a cache hit from latency. A time-of-day change can also alter live pages; archive both the request and raw response.

## Reproducible experiment recipe

1. Freeze URL/question pairs, provider operations, source expectations, output budgets, and evaluator before measuring. Give search-discovery and known-URL tracks separate denominators.
2. State each provider's native TTL policy in the manifest. Use zero-age requests only when the vendor supports them, and label Parallel's 600-second floor and native-tool defaults.
3. Interleave calls across providers, record wall-clock start and elapsed time, and keep HTTP and embedded errors in the denominator. The SDK makes one attempt; any outer retry belongs in the manifest.
4. Save the full trace, including request JSON, raw response, HTTP status, and provider warnings. Keep keys out of traces. Check an actual source page before judging whether a missing passage is an extraction failure.
5. Report retrieval success, evidence sufficiency, content size, latency, and cost separately. Use matched paired analysis and disclose sample size, changed pages, judge uncertainty, and partial runs.

Run `python tests/e2e.py` for a local repeatable fixture exercise. It writes `artifacts/e2e-trace.json` with the exact calls, fake-provider responses, and MCP tool enumeration. It does not claim live provider compatibility, which requires credentials and a separate opt-in run.
