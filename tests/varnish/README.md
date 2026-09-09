# Varnish cache regressions

Run `python3 tests/varnish/run.py` with Helm and Docker installed and Docker running.
The default image is `varnish:7.5.0`, matching the Varnish chart 1.1.1 appVersion.
Set `VARNISH_IMAGE` to test another version.

The runner renders the actual Wikibase Varnish template and substitutes only the
backend address and port with the varnishtest server. It verifies:

- Cached public pages survive HTTP 503 and connection failures after their TTL
  and Varnish's default ten-second grace have expired, and refresh after recovery.
- Private, no-store, no-cache, and Set-Cookie responses are not reused.
- Authorization headers, MediaWiki login cookies, and POST requests bypass caching.

The tests run entirely inside a disposable container; they do not contact the portal.
