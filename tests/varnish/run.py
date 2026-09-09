#!/usr/bin/env python3
"""Render the chart and run cache outage regressions with varnishtest.

Requires helm and Docker. Run from any directory: python3 tests/varnish/run.py
Override VARNISH_IMAGE to test another Varnish release.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[2]
rendered = subprocess.check_output([
    'helm', 'template', 'test', str(ROOT / 'charts/wikibase'),
    '--set', 'global.baseDomain=example.test', '--set', 'varnish.enabled=true',
    '--set', 'apache.port=8080', '--show-only', 'templates/varnish.yaml',
], text=True)
vcl = textwrap.dedent(rendered.split('vclConfig: |\n', 1)[1].split('\n      resources:', 1)[0])
vcl = vcl.replace('"test"', '"${s1_addr}"').replace('"8080"', '"${s1_port}"')


def request(body, headers=''):
    return '''client c1 {
    txreq %s
    rxresp
    expect resp.status == 200
    expect resp.body == "%s"
} -run
''' % (headers, body)


def scenario(name, server, clients):
    return ('varnishtest "%s"\nserver s1 {\n%s\n} -start\n' % (name, server)
            + 'varnish v1 -vcl {\n' + vcl + '\n} -start\n'
            + clients + '\nserver s1 -wait\n')

cases = {}
for failure, response in [('http-error', 'txresp -status 503 -body "unavailable"'),
                          ('connection-failure', 'close')]:
    server = '''rxreq
    txresp -hdr "Cache-Control: public, max-age=1" -hdr "Connection: close" -body "cached"
    close
    accept
    rxreq
    %s
    accept
    rxreq
    txresp -hdr "Cache-Control: public, max-age=60" -body "recovered"
''' % (response + ('\n    close' if failure == 'http-error' else ''))
    clients = (request('cached') + 'delay 12\n' + request('cached')
               + 'delay 0.5\n' + request('cached') + 'delay 0.5\n' + request('recovered'))
    cases[failure] = scenario(failure, server, clients)

for name, header in [('private', 'Cache-Control: private, max-age=60'),
                     ('no-store', 'Cache-Control: no-store, max-age=60'),
                     ('no-cache', 'Cache-Control: no-cache, max-age=60'),
                     ('set-cookie', 'Set-Cookie: session=secret')]:
    server = ('rxreq\n txresp -hdr "%s" -body "first"\n'
              'rxreq\n txresp -hdr "%s" -body "second"\n') % (header, header)
    cases[name] = scenario(name, server, request('first') + request('second'))

for name, headers in [('authorization', '-hdr "Authorization: Bearer test"'),
                      ('login-cookie', '-hdr "Cookie: my_wikiUserID=123"'),
                      ('post', '-req POST')]:
    server = '''rxreq
    txresp -hdr "Cache-Control: public, max-age=60" -body "first"
    rxreq
    txresp -hdr "Cache-Control: public, max-age=60" -body "second"
'''
    cases[name] = scenario(name, server, request('first', headers) + request('second', headers))

# Anonymous entity/article views: MediaWiki sends s-maxage together with a
# Set-Cookie, so the cookie must be dropped for the response to be cached at all.
# The negative cases below are the important ones: a response the backend marked
# private, no-cache or no-store must never be cached just because it is a /wiki/
# URL, and neither must a login page that is legitimately setting a session.
SC = '-hdr "Set-Cookie: session=secret"'
SMAXAGE = 'Cache-Control: s-maxage=30, must-revalidate, max-age=0'


def two_hits(header):
    return ('rxreq\n    txresp -hdr "%s" %s -body "first"\n'
            'rxreq\n    txresp -hdr "%s" %s -body "second"\n'
            % (header, SC, header, SC))


def url_request(body, url):
    return ('client c1 {\n    txreq -url "%s"\n    rxresp\n'
            '    expect resp.status == 200\n    expect resp.body == "%s"\n} -run\n'
            % (url, body))


# Cacheable: the cookie is dropped, so the second request is served from cache
# and the backend (scripted for exactly one request) is never asked again.
cases['wiki-set-cookie-cached'] = scenario(
    'wiki-set-cookie-cached',
    'rxreq\n    txresp -hdr "%s" %s -body "first"\n' % (SMAXAGE, SC),
    url_request('first', '/wiki/Item:Q1') + url_request('first', '/wiki/Item:Q1'))

# The stripped Set-Cookie must not reach the client either.
cases['wiki-set-cookie-stripped'] = scenario(
    'wiki-set-cookie-stripped',
    'rxreq\n    txresp -hdr "%s" %s -body "first"\n' % (SMAXAGE, SC),
    'client c1 {\n    txreq -url "/wiki/Item:Q1"\n    rxresp\n'
    '    expect resp.status == 200\n    expect resp.http.set-cookie == <undef>\n} -run\n')

# Not cacheable: the backend explicitly forbids it, even on a /wiki/ URL.
for _name, _header in [('wiki-private', 'Cache-Control: private, must-revalidate, max-age=0'),
                       ('wiki-no-store', 'Cache-Control: no-store, max-age=60'),
                       ('wiki-no-cache', 'Cache-Control: no-cache, max-age=60')]:
    cases[_name] = scenario(_name, two_hits(_header),
                            url_request('first', '/wiki/Item:Q1')
                            + url_request('second', '/wiki/Item:Q1'))

# Not cacheable: a login page setting a real session must keep its cookie.
cases['wiki-login-not-cached'] = scenario(
    'wiki-login-not-cached', two_hits('Cache-Control: s-maxage=30'),
    url_request('first', '/wiki/Special:UserLogin?returnto=Item:Q1')
    + url_request('second', '/wiki/Special:UserLogin?returnto=Item:Q1'))

# Entity redirects are cached beyond the backend's own short s-maxage, so the
# backend is asked once for two requests.
cases['entity-redirect-cached'] = scenario(
    'entity-redirect-cached',
    'rxreq\n    txresp -status 301 -hdr "Location: /wiki/Item:Q1" '
    '-hdr "Cache-Control: s-maxage=30" %s -body "moved"\n' % SC,
    'client c1 {\n    txreq -url "/entity/Q1"\n    rxresp\n'
    '    expect resp.status == 301\n} -run\n\n'
    'client c1 {\n    txreq -url "/entity/Q1"\n    rxresp\n'
    '    expect resp.status == 301\n} -run\n')

# Expensive uncacheable URLs are refused for anonymous users before MediaWiki is
# reached at all; the scripted backend expects no request.
for _name, _url in [('block-oldid', '/w/index.php?oldid=12345&title=Item:Q1'),
                    ('block-diff', '/w/index.php?diff=prev&oldid=12345'),
                    ('block-random', '/wiki/Special:Random')]:
    # No "server s1 -wait" here: the backend is deliberately never contacted, so
    # waiting for it to finish a request would hang. s1 exists only to supply the
    # ${s1_addr}/${s1_port} macros the backend definitions need.
    cases[_name] = ('varnishtest "%s"\nserver s1 {\n} -start\n' % _name
                    + 'varnish v1 -vcl {\n' + vcl + '\n} -start\n'
                    + 'client c1 {\n    txreq -url "%s"\n    rxresp\n'
                      '    expect resp.status == 403\n} -run\n' % _url)


with tempfile.TemporaryDirectory(prefix='varnish-grace-') as tmp:
    for name, test in cases.items():
        Path(tmp, name + '.vtc').write_text(test)
    subprocess.run([
        'docker', 'run', '--rm', '-v', tmp + ':/tests:ro',
        '--entrypoint', 'varnishtest', os.environ.get('VARNISH_IMAGE', 'varnish:7.5.0'),
        '-j', '1', *['/tests/' + name + '.vtc' for name in cases],
    ], check=True)
