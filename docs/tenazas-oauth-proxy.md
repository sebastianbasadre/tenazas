# OAuth proxy hardening — repository only

The integrated modules are `workspace.runtime.mcp_oauth_config` and
`workspace.runtime.mcp_oauth_proxy`. They derive from the real sanitized staging
handoff in `tenazas-oauth-handoff/`, which remains unchanged. No configuration,
certificate, bearer value, deployment or service startup hook is supplied.

Install the optional `mcp-oauth` dependency group for the proxy. PyJWT is pinned
to 2.15.1 with its crypto extra; this is the tested repository dependency, not a
claim about the previously deployed staging version. Test keys and certificates
are generated in temporary directories; JWKS fixtures are local.

## Explicit configuration

`ProxyConfig` requires issuer, audience, JWKS URL, upstream URL, an exact upstream
URL allowlist, the public resource-metadata URL, an absolute internal bearer path
and expected numeric UID/GID. HTTPS URL fields reject credentials, queries,
fragments and unsafe header characters. No Auth0 tenant, production URL or
runtime credential is inferred. An operator-owned JSON config is accepted by
the future module CLI; it is not checked into this repository.

Defaults: connect timeout 3 seconds, upstream read timeout 5 seconds, client/TLS
timeout 5 seconds, request limit 16 KiB, response limit 1 MiB, 16 concurrent
workers, global rate 120 requests per 60 seconds and JWKS cache TTL 300 seconds.
JWKS responses are bounded at 64 KiB and 64 keys. Timeouts are socket timeouts,
not an end-to-end deadline; concurrency and rate limits additionally bound load.

## Authentication and transport

JWT validation uses fixed RS256, configured issuer/audience, required exp/iat/iss/aud
and standard PyJWT signature/time checks. Scope must be a string with the exact
`read:tenazas` element. Invalid tokens or malformed scope types return 401
`invalid_token`; a valid string scope without the permission returns 403
`insufficient_scope`. Validator/JWKS unavailability returns 503, without revealing
details. The challenge points to the configured public metadata URL.

The bounded JWKS cache does not refresh for every unknown kid, preventing a client
from forcing a JWKS fetch per request. New signing keys become visible on cache
expiry. Issuer key rotation must therefore publish new keys sufficiently early or
use an explicitly reviewed cache TTL; no invalid-token fallback bypasses validation.
Expired cache refresh failure fails closed rather than accepting unvalidated tokens.

The public JSON-RPC allowlist remains exactly initialize, notifications/initialized,
ping, server/discover and tools/list. All other methods, including tools/call, require
JWT validation. Request method must be a string. IDs, protocol, arguments and metadata
are forwarded intact and validated by the existing gateway, which exposes exactly
six read-only tools and one coherent snapshot generation per call.

The upstream transport uses direct HTTPS with default certificate verification,
an exact URL allowlist and no ambient proxies or redirect following. Every
301/302/303/307/308 response is rejected. Only explicit JSON headers and the
internal bearer are constructed; the HTTP library adds standard framing headers.
The inbound JWT Authorization header is never forwarded. Body contents are
unchanged: a client that embeds a token in `_meta` sends that field to the gateway,
so body preservation is not a claim of arbitrary secret removal.

Unexpected upstream statuses are translated to a fixed 502; internal bearer
challenges and arbitrary response headers stay private. Accepted upstream statuses
are 200 and 202, including the empty initialized notification response. Response
size is bounded even without Content-Length. No response body is logged.

## HTTP boundary and credentials

The proxy uses HTTP/1.1 with explicit Connection: close for every response, including
rejects. It rejects any Origin, duplicate Authorization, Transfer-Encoding,
duplicate/invalid Content-Length, oversized requests and non-JSON media types.
JSON media type permits application/json with an optional UTF-8 charset only.
It reads the exact declared body under the client timeout. Expect: 100-continue is
rejected. The metadata endpoint supports GET/HEAD; HEAD writes no body. PUT, DELETE,
PATCH and OPTIONS return 405. MCP requests use POST /mcp.

Linux bearer-file policy requires a regular file, no symlink anywhere in its path,
one hardlink, configured UID/GID, mode 0400, 0600 or 0640 and a maximum of 256 bytes.
Mode 0640 requires the same exact UID/GID match; group-write, world permissions
and special bits are never permitted.
The descriptor identity is checked against lstat. Content must match the gateway's
32–128 ASCII alphanumeric token contract. No ownership or permission repair occurs.
Windows credential policy is deliberately unsupported; transport tests inject a
synthetic reader, and Linux tests exercise the real policy.

TLS handshakes occur in bounded worker threads under timeout, not on the accepting
socket. Logs contain fixed events and numeric HTTP status only; no client values,
queries, headers, request/response bodies or exception tracebacks are logged.

## Validation and pending staging work

Run the proxy tests together with all four existing MCP test modules. On Linux,
credential/cache policy checks must run without skips. Local HTTPS transport tests
use generated certificates and test only loopback. JWT/JWKS tests never contact
Auth0 or any real issuer. Compatibility tests route proxy requests to a local
gateway with synthetic snapshots and credentials.

Before a separately authorized staging E2E, verify the real configuration alignment,
JWT/key rotation behavior, the chosen dependency versions and tunnel behavior with
explicit connection closure. Keep producer deployment, source wiring, single-writer
changes and boot enablement outside this phase. No NAS or VM changes are authorized
by this repository implementation.
