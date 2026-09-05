# Publishing SentimentFX to the official MCP registry

`server.json` at the repo root is the registry manifest. It validates against
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
(checked with `jsonschema` — see "Validate" below).

The registry is in **preview**; breaking changes and data resets are possible
before GA.

## Before you publish

The manifest advertises `https://api.sentimentfx.org/mcp`, and the registry
requires a remote server to be **publicly reachable at that exact URL**. Confirm
with a real handshake rather than a status code:

```bash
curl -s -X POST https://api.sentimentfx.org/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' \
  -D- -o /dev/null | grep -i 'HTTP\|mcp-session-id'
```

Expect `HTTP/1.1 200` **and** an `mcp-session-id` header. A `404` means the
old path-doubling mount is back (the endpoint would be on `/mcp/mcp` — see
CLAUDE.md's MCP gotchas); a `421` means the Host isn't in `MCP_ALLOWED_HOSTS`.

A `307 -> /mcp/` means the `_MCPBarePathFix` ASGI middleware in `main.py` has
been removed or is no longer reached. Starlette compiles a Mount at `/mcp` to
`^/mcp/(?P<path>.*)$`, so bare `/mcp` misses it and falls through to
`redirect_slashes`. That redirect is survivable for TypeScript clients
(`fetch` follows redirects by default, which is why Claude Desktop/Code never
surfaced it) but **not** for the Python MCP SDK, which is built on `httpx` —
that does not follow redirects by default, unlike `requests`. The registry's
own reachability check may not follow it either. Restore the middleware
rather than changing the advertised URL: `server.json`, the developer portal
configs and the landing page all publish the bare path.

## Namespace: two options

The `name` field must be a namespace you can prove you own.

### Option A — `org.sentimentfx/market-sentiment` (what server.json uses)

Better identity for a commercial product, but needs a DNS TXT record and an
Ed25519 key.

The `org.sentimentfx` half is fixed — it's `sentimentfx.org` reversed, which is
what the DNS proof binds to. Only the part after the slash is a free choice, and
it's an identifier, not a display name: registries show the `title` field
(`SentimentFX`). Hence `market-sentiment` rather than a stuttering
`org.sentimentfx/sentimentfx`. **Don't rename after publishing** — that string
lives in every user's client config, so a change means they all re-install.

```bash
# 1. generate a keypair
openssl genpkey -algorithm Ed25519 -out mcp-key.pem

# 2. public key, base64 — goes in DNS
openssl pkey -in mcp-key.pem -pubout -outform DER | tail -c 32 | base64

# 3. add a TXT record on sentimentfx.org:
#      v=MCPv1; k=ed25519; p=<that base64 public key>
#    (wait for propagation: dig +short TXT sentimentfx.org)

# 4. private key, hex — this is what the CLI wants
openssl pkey -in mcp-key.pem -outform DER | tail -c 32 | xxd -p -c 64

# 5. log in and publish
mcp-publisher login dns --domain sentimentfx.org --private-key <hex-from-step-4>
mcp-publisher publish
```

Keep `mcp-key.pem` out of the repo — it is a publishing credential.
(`.gitignore` already covers `*.pem`; confirm before committing.)

### Option B — `io.github.shaelegowski7/sentimentfx`

No DNS work; authenticates against the GitHub account that owns the repo.

```bash
mcp-publisher login github
mcp-publisher publish
```

If you take this route, change `name` in `server.json` to
`io.github.shaelegowski7/sentimentfx` first. Note the name **is** the server's
identity in the registry — switching later means republishing under a new one,
so pick before the first publish rather than after.

## Validate before publishing

```bash
curl -s https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json -o /tmp/s.json
python -c "
import json, jsonschema
jsonschema.Draft7Validator(json.load(open('/tmp/s.json'))).validate(json.load(open('server.json')))
print('valid')"
```

Two constraints that are easy to trip:
- `description` is capped at **100 characters** (the first draft of this file
  was ~330 and would have been rejected).
- `name` must match `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`.

## Other directories

The official registry is the one with a schema and a CLI. These are separate,
each with its own submission flow — worth doing, but they are manual:

- **mcp.so**
- **PulseMCP**
- **Smithery**
- **awesome-mcp-servers** (GitHub PR)

For those, the useful blurb is:

> SentimentFX — FinBERT-scored news sentiment plus matched OHLCV price history
> for 42 tickers across crypto, FX, US equities, ETFs and commodity futures.
> 178,000+ headlines from 12,500+ sources since 2019. Six tools; free key with
> 100 calls, no card required. The correlation tool reports its own p-value and
> confidence interval, and returns "inconclusive" when that is the honest
> answer — which, on most tickers, it is.

## Keeping it current

Bump `version` in `server.json` and re-run `mcp-publisher publish` when the
tool surface changes. The manifest currently describes 6 tools:
`list_tickers`, `get_usage` (both free) and `get_sentiment`, `get_summary`,
`get_prices`, `get_correlation` (billed, mirroring `/v1/*`).
