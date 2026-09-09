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

with tempfile.TemporaryDirectory(prefix='varnish-grace-') as tmp:
    for name, test in cases.items():
        Path(tmp, name + '.vtc').write_text(test)
    subprocess.run([
        'docker', 'run', '--rm', '-v', tmp + ':/tests:ro',
        '--entrypoint', 'varnishtest', os.environ.get('VARNISH_IMAGE', 'varnish:7.5.0'),
        '-j', '1', *['/tests/' + name + '.vtc' for name in cases],
    ], check=True)
