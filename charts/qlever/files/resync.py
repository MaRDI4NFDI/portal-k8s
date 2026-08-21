#!/usr/bin/env python3
"""Reconcile individual Wikibase entities into a QLever triplestore.

The streaming updater (`qlever update-wikidata`) is delta-driven: it only ever
applies the deletes and inserts that an SSE event carries.  If it crashes, is
restarted with empty Flink state, or the stream skips a batch, individual
entities silently drift out of sync and nothing ever repairs them.

This tool reconciles by *state* rather than by event.  For each entity it

  1. fetches the authoritative RDF from ``Special:EntityData/<id>.nt?flavor=dump``
  2. applies the WDQS Munger transformation, so the result is byte-for-byte
     what the streaming updater would have written
  3. CONSTRUCTs the entity's current scope out of QLever
  4. diffs the two, comparing literals by *value* (QLever folds numeric
     literals, so a lexical diff would rewrite every quantity on every run)
  5. issues one DELETE + INSERT to make the store match

Only triples the entity owns are ever deleted: the entity itself, its statement
nodes, and nodes with ``schema:about <entity>`` (sitelinks and the legacy
entity-data nodes left behind by the initial dump import).  Reference and value
nodes are shared between entities and are therefore insert-only.

Usage::

    resync Q42 P31              # reconcile these entities
    resync --check Q42          # report drift, change nothing
    resync --from-file ids.txt  # one id per line, '#' comments allowed
    resync --changed-since 24h  # everything edited in the last 24 hours

Connection details default from the environment (QLEVER_HOST, QLEVER_PORT,
QLEVER_ACCESS_TOKEN, ENTITY_DATA_URL, WIKIBASE_CONCEPT_URI, WIKIBASE_API_URL),
so inside the resync pod the bare form above is all that is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

VERSION = "1.0"
USER_AGENT = f"qlever-resync/{VERSION}"

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
ONTOLOGY_NS = "http://wikiba.se/ontology#"
ONT_STATEMENT = ONTOLOGY_NS + "Statement"
ONT_REFERENCE = ONTOLOGY_NS + "Reference"
ONT_ITEM = ONTOLOGY_NS + "Item"
ONT_WIKIGROUP = ONTOLOGY_NS + "wikiGroup"
SCHEMA_NS = "http://schema.org/"
SCHEMA_ABOUT = SCHEMA_NS + "about"
SCHEMA_ARTICLE = SCHEMA_NS + "Article"
SCHEMA_VERSION = SCHEMA_NS + "version"
SCHEMA_DATE_MODIFIED = SCHEMA_NS + "dateModified"
SCHEMA_NAME = SCHEMA_NS + "name"
SKOS_PREF_LABEL = "http://www.w3.org/2004/02/skos/core#prefLabel"
ONTOLEX_NS = "http://www.w3.org/ns/lemon/ontolex#"
XSD = "http://www.w3.org/2001/XMLSchema#"
XSD_STRING = XSD + "string"
XSD_DATETIME = XSD + "dateTime"

# Munger.SKIPPED_TYPES -- rdf:type triples with these objects are dropped
# because they are ubiquitous and uninteresting.
SKIPPED_TYPES = {
    ONT_ITEM,
    ONTOLEX_NS + "LexicalEntry",
    ONTOLEX_NS + "Form",
    ONTOLEX_NS + "LexicalSense",
}

NUMERIC_DATATYPES = {
    XSD + n
    for n in (
        "decimal", "integer", "double", "float", "long", "int", "short",
        "byte", "nonNegativeInteger", "positiveInteger", "nonPositiveInteger",
        "negativeInteger", "unsignedLong", "unsignedInt", "unsignedShort",
        "unsignedByte",
    )
}

# Munger.trimLargeObject: literals longer than Short.MAX_VALUE are truncated.
MAX_LITERAL_LENGTH = 32767

ID_RE = re.compile(r"^[QPLMES]\d+$")


# --------------------------------------------------------------------------
# N-Triples
#
# Terms are represented as tuples so they are hashable and cheap to compare:
#   ("U", iri) | ("B", label) | ("L", lexical, datatype_or_None, lang_or_None)
# --------------------------------------------------------------------------

_ESCAPES = {"t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f",
            '"': '"', "'": "'", "\\": "\\"}


def _unescape(s: str) -> str:
    if "\\" not in s:
        return s
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = s[i + 1]
        if e == "u":
            out.append(chr(int(s[i + 2:i + 6], 16)))
            i += 6
        elif e == "U":
            out.append(chr(int(s[i + 2:i + 10], 16)))
            i += 10
        else:
            out.append(_ESCAPES.get(e, e))
            i += 2
    return "".join(out)


def _term(line: str, i: int):
    """Parse one term starting at or after position `i`."""
    n = len(line)
    while i < n and line[i] in " \t":
        i += 1
    c = line[i]
    if c == "<":
        j = line.index(">", i + 1)
        return ("U", _unescape(line[i + 1:j])), j + 1
    if c == "_":
        j = i
        while j < n and line[j] not in " \t":
            j += 1
        return ("B", line[i + 2:j]), j
    if c == '"':
        j = i + 1
        while True:
            if line[j] == "\\":
                j += 2
                continue
            if line[j] == '"':
                break
            j += 1
        lex = _unescape(line[i + 1:j])
        j += 1
        if j < n and line[j] == "@":
            k = j + 1
            while k < n and (line[k].isalnum() or line[k] == "-"):
                k += 1
            return ("L", lex, None, line[j + 1:k]), k
        if line[j:j + 2] == "^^":
            k = line.index(">", j + 3)
            return ("L", lex, _unescape(line[j + 3:k]), None), k + 1
        return ("L", lex, None, None), j
    raise ValueError(f"cannot parse term at offset {i}: {line[i:i + 40]!r}")


def parse_nt(text: str):
    """Yield (subject, predicate, object) triples from an N-Triples document."""
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            s, i = _term(line, 0)
            p, i = _term(line, i)
            o, _ = _term(line, i)
        except (ValueError, IndexError) as exc:
            raise ValueError(f"malformed N-Triples on line {lineno}: {exc}") from exc
        yield (s, p, o)


_LITERAL_ESCAPES = {ord("\\"): "\\\\", ord('"'): '\\"', ord("\n"): "\\n",
                    ord("\r"): "\\r", ord("\t"): "\\t"}


def _escape_literal(s: str) -> str:
    s = s.translate(_LITERAL_ESCAPES)
    # Remaining C0 controls have no short form; N-Triples requires \u escapes.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]",
                  lambda m: "\\u%04X" % ord(m.group()), s)


def ser_term(t) -> str:
    kind = t[0]
    if kind == "U":
        return "<" + t[1].replace("\\", "\\u005C").replace(">", "\\u003E") + ">"
    if kind == "B":
        return "_:" + t[1]
    out = '"' + _escape_literal(t[1]) + '"'
    if t[3]:
        return out + "@" + t[3]
    if t[2]:
        return out + "^^<" + t[2] + ">"
    return out


def ser_triple(t) -> str:
    return " ".join(ser_term(x) for x in t) + " ."


# --------------------------------------------------------------------------
# URI scheme
# --------------------------------------------------------------------------

class Uris:
    """The Wikibase URI namespaces, derived from the concept base URI."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.entity = self.base + "/entity/"
        self.statement = self.base + "/entity/statement/"
        self.reference = self.base + "/reference/"
        self.value = self.base + "/value/"
        self.prop = self.base + "/prop/"
        self.novalue = self.base + "/prop/novalue/"
        self.wiki = self.base + "/wiki/"
        self.entity_data = self.base + "/wiki/Special:EntityData/"
        # The initial dump import used http:// where the stream uses https://
        # (or vice versa). Those stale nodes must be cleaned up too.
        other = ("http://" + self.base.split("://", 1)[1]
                 if self.base.startswith("https://")
                 else "https://" + self.base.split("://", 1)[1])
        self.legacy_entity_data = other + "/wiki/Special:EntityData/"

    def entity_uri(self, eid: str) -> str:
        return self.entity + eid

    def statement_range(self, eid: str):
        """Half-open IRI range covering exactly this entity's statement nodes.

        '.' (0x2E) is the next codepoint after '-' (0x2D), and every statement
        id is '<eid>-<uuid>', so the range excludes e.g. Q13990-... for Q1399.
        """
        return self.statement + eid + "-", self.statement + eid + "."


# --------------------------------------------------------------------------
# The munge (port of org.wikidata.query.rdf.tool.rdf.Munger)
# --------------------------------------------------------------------------

def _trim(term):
    if term[0] == "L" and len(term[1]) > MAX_LITERAL_LENGTH:
        return ("L", term[1][:MAX_LITERAL_LENGTH], term[2], term[3])
    return term


def munge(triples, eid: str, uris: Uris):
    """Transform a Special:EntityData dump the way the WDQS Munger does."""
    entity = uris.entity_uri(eid)
    entity_data = uris.entity_data + eid
    triples = list(triples)

    by_subject = defaultdict(list)
    for t in triples:
        by_subject[t[0]].append(t)

    # Munger keeps a statement/reference/value node only once it has seen a
    # link to it from something already known to belong to the entity, and
    # restores previously-unknown ones when the link shows up later.  Computing
    # reachability up front is equivalent and order-independent.
    node_namespaces = (uris.statement, uris.reference, uris.value)
    valid = {("U", entity)}
    frontier = [("U", entity)]
    while frontier:
        for (_, _, o) in by_subject.get(frontier.pop(), ()):
            if o[0] == "U" and o not in valid and o[1].startswith(node_namespaces):
                valid.add(o)
                frontier.append(o)

    # A subject becomes a sitelink once it is typed schema:Article.
    sitelinks = {s for s, ts in by_subject.items()
                 if any(p[1] == RDF_TYPE and o == ("U", SCHEMA_ARTICLE)
                        for (_, p, o) in ts)}

    kept, moved = [], []
    for t in triples:
        s, p, o = t
        pu = p[1]

        if s[0] == "B":
            # Blank nodes are class declarations (wdno: OWL restrictions), not
            # tied to any one entity.
            kept.append(t)
            continue

        su = s[1]

        if su.startswith(uris.entity_data):
            # Every entity-data triple is dropped; a few are re-attached to the
            # entity itself at the end.
            if su == entity_data and (pu in (SCHEMA_VERSION, SCHEMA_DATE_MODIFIED)
                                      or pu.startswith(ONTOLOGY_NS)):
                moved.append((("U", entity), p, o))
            continue

        if su.startswith(uris.statement):
            if pu == RDF_TYPE and o == ("U", ONT_STATEMENT):
                continue
            if s in valid:
                kept.append(t)
            continue

        if su.startswith(uris.reference):
            if pu == RDF_TYPE and o == ("U", ONT_REFERENCE):
                continue
            if s in valid:
                kept.append(t)
            continue

        if su.startswith(uris.value):
            if s in valid:
                kept.append(t)
            continue

        if su.startswith(uris.entity):
            if su != entity:
                # Some dump flavors describe neighbouring entities too.
                continue
            if pu == RDF_TYPE and o[0] == "U" and o[1] in SKIPPED_TYPES:
                continue
            if pu in (SCHEMA_NAME, SKOS_PREF_LABEL):
                # Duplicates of rdfs:label.
                continue
            kept.append(t)
            continue

        if su.startswith(uris.prop):
            if su.startswith(uris.novalue) or pu == RDF_TYPE:
                kept.append(t)
            continue

        if pu == ONT_WIKIGROUP or s in sitelinks:
            kept.append(t)
        # Anything else is genuinely unknown and gets dropped, as upstream does.

    kept.extend(moved)
    return [(s, p, _trim(o)) for (s, p, o) in kept]


# --------------------------------------------------------------------------
# Value-normalised comparison
# --------------------------------------------------------------------------

_DATETIME_RE = re.compile(
    r"^(-?\d+)-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


def normalise(term):
    """Canonical form for diffing.

    QLever folds numeric literals into internal doubles and re-serialises them
    with ~13 significant digits and a different datatype ("1"^^xsd:decimal comes
    back as 1.0^^xsd:double).  Comparing lexically would therefore report drift
    on every numeric literal forever.  Compare by value instead.
    """
    if term[0] != "L":
        return term
    lex, datatype, lang = term[1], term[2], term[3]
    if datatype in NUMERIC_DATATYPES:
        try:
            return ("N", float("%.12g" % float(lex)))
        except (ValueError, OverflowError):
            pass
    if datatype == XSD_DATETIME:
        m = _DATETIME_RE.match(lex)
        if m:
            y, mo, d, h, mi, sec, tz = m.groups()
            return ("T", int(y), int(mo), int(d), int(h), int(mi), int(sec), tz or "Z")
    if datatype == XSD_STRING:
        datatype = None
    return ("L", lex, datatype, lang)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def http(url, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise HttpError(exc.code, f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise HttpError(None, f"cannot reach {url}: {exc.reason}") from exc


class Store:
    """The QLever SPARQL endpoint."""

    def __init__(self, host, port, access_token, timeout):
        # A full URL is accepted too, which is handy for pointing at a remote
        # endpoint from a laptop.
        self.endpoint = (host if host.startswith(("http://", "https://"))
                         else f"http://{host}:{port}")
        self.access_token = access_token
        self.timeout = timeout
        self.queries = 0
        self.updates = 0

    def construct(self, query):
        self.queries += 1
        body = http(self.endpoint, data=query.encode("utf-8"),
                    headers={"Accept": "application/n-triples",
                             "Content-type": "application/sparql-query"},
                    timeout=self.timeout)
        if body.lstrip().startswith("{"):
            raise HttpError(None, f"query failed: {body[:400]}")
        return list(parse_nt(body))

    def select(self, query):
        self.queries += 1
        body = http(self.endpoint, data=query.encode("utf-8"),
                    headers={"Accept": "application/sparql-results+json",
                             "Content-type": "application/sparql-query"},
                    timeout=self.timeout)
        result = json.loads(body)
        if "exception" in result:
            raise HttpError(None, f"query failed: {result['exception'][:400]}")
        return result["results"]["bindings"]

    def update(self, operations):
        if not operations:
            return
        self.updates += 1
        url = self.endpoint + "?" + urllib.parse.urlencode(
            {"access-token": self.access_token})
        body = http(url, data=";\n".join(operations).encode("utf-8"),
                    headers={"Content-Type": "application/sparql-update"},
                    timeout=self.timeout)
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            return
        if "exception" in result:
            raise HttpError(None, f"update failed: {result['exception'][:400]}")


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

class Drift:
    def __init__(self, eid):
        self.eid = eid
        self.deleted = []           # everything that will be removed (for display)
        self.inserted = []          # triples that must be added
        self.delete_triples = []    # removed by exact match
        self.delete_patterns = []   # (subject, predicate) groups cleared wholesale
        self.shared_missing = []
        self.skipped_bnodes = 0
        self.missing_upstream = False

    @property
    def clean(self):
        return not self.deleted and not self.inserted and not self.shared_missing

    def summary(self):
        if self.clean:
            return "in sync"
        parts = []
        if self.deleted:
            parts.append(f"-{len(self.deleted)}")
        if self.inserted:
            parts.append(f"+{len(self.inserted)}")
        if self.shared_missing:
            parts.append(f"+{len(self.shared_missing)} shared")
        return " ".join(parts)


def fetch_target(entity_data_url, eid, uris, timeout):
    """Fetch and munge the authoritative RDF for one entity.

    Returns None when the entity does not exist upstream (deleted or never
    created), in which case everything the store holds for it must go.
    """
    url = f"{entity_data_url}{eid}.nt?flavor=dump"
    try:
        body = http(url, timeout=timeout)
    except HttpError as exc:
        if exc.status == 404:
            return None
        raise
    triples = list(parse_nt(body))
    # Redirects resolve to a different entity; reconciling would then copy the
    # target's data onto this id.
    if not any(t[0] == ("U", uris.entity_uri(eid)) for t in triples):
        raise HttpError(None, f"{url} describes no {uris.entity_uri(eid)} "
                              "(redirect or unexpected flavor)")
    return munge(triples, eid, uris)


def store_scope_query(eid, uris):
    """CONSTRUCT everything the store currently holds *for* this entity.

    Three sources: the entity itself and its entity-data nodes; every statement
    node in the entity's IRI range (a range scan, so orphaned statements left
    behind by a half-applied delete are found too); and anything pointing at the
    entity with schema:about, which covers sitelinks.
    """
    entity = uris.entity_uri(eid)
    lo, hi = uris.statement_range(eid)
    return f"""PREFIX schema: <{SCHEMA_NS}>
CONSTRUCT {{ ?s ?p ?o }} WHERE {{
  {{
    VALUES ?s {{ <{entity}> <{uris.entity_data}{eid}> <{uris.legacy_entity_data}{eid}> }}
    ?s ?p ?o
  }} UNION {{
    ?s ?p ?o .
    FILTER(?s >= <{lo}> && ?s < <{hi}>)
  }} UNION {{
    ?s schema:about <{entity}> .
    ?s ?p ?o
  }}
}}"""


def split_scope(triples, eid, uris):
    """Partition munged triples into owned / shared / skipped.

    Owned triples may be deleted.  Shared ones (reference nodes, value nodes,
    property ontology, the wiki group) are referenced by other entities, so they
    are insert-only.  Triples touching blank nodes are skipped entirely: their
    labels are not stable across systems, so re-inserting them would duplicate.
    """
    entity = uris.entity_uri(eid)
    stmt_prefix = uris.statement + eid + "-"
    sitelinks = {s for (s, p, o) in triples
                 if p[1] == SCHEMA_ABOUT and o == ("U", entity)}

    owned, shared, skipped = [], [], 0
    for t in triples:
        if t[0][0] == "B" or t[2][0] == "B":
            skipped += 1
            continue
        su = t[0][1]
        if su == entity or su.startswith(stmt_prefix) or t[0] in sitelinks:
            owned.append(t)
        else:
            shared.append(t)
    return owned, shared, skipped


def missing_shared(store, shared):
    """Which of these shared triples are not in the store yet?

    Binding whole triples with VALUES (?s ?p ?o) makes QLever plan a full scan,
    so bind only the subjects -- each is a reference, value or property node
    with a handful of triples -- and compare the rest here.
    """
    if not shared:
        return []
    subjects = sorted({t[0] for t in shared})
    present = set()
    for chunk in _chunks(subjects, 200):
        values = " ".join(ser_term(s) for s in chunk)
        for (s, p, o) in store.construct(
                f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o }}"):
            present.add((s, p, normalise(o)))
    return [t for t in shared
            if (t[0], t[1], normalise(t[2])) not in present]


def diff(store, eid, uris, target, timeout):
    """Work out what has to change for one entity."""
    drift = Drift(eid)
    # The UNION branches of the scope query overlap (an entity-data node is
    # matched both by name and by schema:about), so deduplicate.
    store_triples = set(store.construct(store_scope_query(eid, uris)))

    if target is None:
        drift.missing_upstream = True
        target_owned, target_shared = [], []
    else:
        target_owned, target_shared, drift.skipped_bnodes = split_scope(
            set(target), eid, uris)

    # Compare per (subject, predicate) group, by normalised value.
    target_by_key = defaultdict(dict)
    for (s, p, o) in target_owned:
        target_by_key[(s, p)][normalise(o)] = o
    store_by_key = defaultdict(dict)
    for (s, p, o) in store_triples:
        store_by_key[(s, p)][normalise(o)] = o

    for key in set(target_by_key) | set(store_by_key):
        want = target_by_key.get(key, {})
        have = store_by_key.get(key, {})
        if set(want) == set(have):
            continue
        s, p = key
        surplus = [have[k] for k in set(have) - set(want)]
        if all(_exactly_matchable(o) for o in surplus):
            # Normal case: remove precisely the objects that should not be
            # there and add precisely the ones that are missing.
            drift.delete_triples.extend((s, p, o) for o in surplus)
            drift.deleted.extend((s, p, o) for o in surplus)
            drift.inserted.extend((s, p, want[k]) for k in set(want) - set(have))
        else:
            # QLever re-serialises numeric and dateTime literals, so the form we
            # read back may not match what is stored.  Clear the whole group by
            # pattern and write it out again.
            drift.delete_patterns.append(key)
            drift.deleted.extend((s, p, o) for o in have.values())
            drift.inserted.extend((s, p, o) for o in want.values())

    drift.shared_missing = missing_shared(store, target_shared)
    return drift


def _exactly_matchable(term):
    """Can this term, as QLever handed it back, be matched again literally?

    Numeric and dateTime literals come back re-serialised from QLever's internal
    representation ("1"^^xsd:decimal reads back as 1.0^^xsd:double), so quoting
    them in a DELETE DATA would not necessarily match the stored triple.
    """
    if term[0] != "L":
        return True
    return term[2] not in NUMERIC_DATATYPES and term[2] != XSD_DATETIME


def build_operations(drift, chunk_size=2000):
    """The SPARQL operations that make the store match the entity, deletes first."""
    operations = []
    for chunk in _chunks(drift.delete_triples, chunk_size):
        body = "\n  ".join(ser_triple(t) for t in chunk)
        operations.append(f"DELETE DATA {{\n  {body}\n}}")
    for chunk in _chunks(drift.delete_patterns, chunk_size):
        values = "\n    ".join(f"({ser_term(s)} {ser_term(p)})" for (s, p) in chunk)
        operations.append(
            f"DELETE {{ ?s ?p ?o }} WHERE {{\n  VALUES (?s ?p) {{\n    {values}\n  }}\n"
            f"  ?s ?p ?o\n}}")
    for chunk in _chunks(drift.inserted + drift.shared_missing, chunk_size):
        body = "\n  ".join(ser_triple(t) for t in chunk)
        operations.append(f"INSERT DATA {{\n  {body}\n}}")
    return operations


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --------------------------------------------------------------------------
# Work lists
# --------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(value: str) -> str:
    """Turn '24h', '2026-08-17' or a full ISO timestamp into an API timestamp."""
    value = value.strip()
    m = _DURATION_RE.match(value)
    if m:
        seconds = int(m.group(1)) * _UNITS[m.group(2).lower()]
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value + "T00:00:00Z"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        return value
    raise ValueError(
        f"cannot read --changed-since {value!r}: expected a duration like "
        "'24h' (s/m/h/d/w), a date '2026-08-17', or '2026-08-17T12:00:00Z'")


def changed_since(api_url, since, namespaces, limit, timeout, log):
    """Entity ids edited, created or logged against since `since`, newest first."""
    ids, seen = [], set()
    params = {
        "action": "query",
        "list": "recentchanges",
        "rcnamespace": "|".join(namespaces),
        "rcprop": "title|timestamp",
        "rctype": "edit|new|log",
        "rclimit": "500",
        "rcdir": "older",
        "rcend": since,
        "format": "json",
        "formatversion": "2",
    }
    while True:
        body = http(api_url + "?" + urllib.parse.urlencode(params), timeout=timeout)
        payload = json.loads(body)
        if "error" in payload:
            raise HttpError(None, f"recentchanges failed: {payload['error']}")
        for change in payload.get("query", {}).get("recentchanges", []):
            title = change.get("title", "")
            eid = title.split(":", 1)[1] if ":" in title else title
            if ID_RE.match(eid) and eid not in seen:
                seen.add(eid)
                ids.append(eid)
        if len(ids) >= limit:
            log(f"stopping at --limit {limit}; more changes remain since {since}")
            return ids[:limit]
        if "continue" not in payload:
            return ids
        params.update(payload["continue"])


def read_ids(path):
    stream = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        ids = []
        for line in stream:
            line = line.split("#", 1)[0].strip()
            if line:
                ids.extend(line.split())
        return ids
    finally:
        if stream is not sys.stdin:
            stream.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="resync",
        description="Reconcile Wikibase entities into a QLever triplestore.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  resync Q42 P31\n"
               "  resync --check Q42\n"
               "  resync --from-file ids.txt\n"
               "  resync --changed-since 24h\n")
    parser.add_argument("ids", nargs="*", metavar="ID",
                        help="entity ids, e.g. Q42 P31")
    parser.add_argument("--from-file", metavar="PATH",
                        help="read ids from a file ('-' for stdin)")
    parser.add_argument("--changed-since", metavar="WHEN",
                        help="reconcile everything edited since WHEN: a duration "
                             "like 24h, a date, or an ISO timestamp")
    parser.add_argument("-n", "--check", action="store_true",
                        help="report drift without changing anything "
                             "(exit 1 if any entity is out of sync)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every triple that changes")
    parser.add_argument("--print-sparql", action="store_true",
                        help="with --check, also print the SPARQL that would "
                             "have been sent")
    parser.add_argument("--limit", type=int, default=10000, metavar="N",
                        help="maximum entities to take from --changed-since "
                             "(default: 10000)")
    parser.add_argument("--namespaces", default=os.environ.get("WIKIBASE_NAMESPACES", "120,122"),
                        help="entity namespaces to scan (default: 120,122)")
    parser.add_argument("--timeout", type=int, default=300, metavar="SECONDS",
                        help="per-request timeout (default: 300)")

    conn = parser.add_argument_group("connection (all default from the environment)")
    conn.add_argument("--host-name", default=os.environ.get("QLEVER_HOST", "localhost"),
                      help="QLever host [QLEVER_HOST]")
    conn.add_argument("--port", default=os.environ.get("QLEVER_PORT", "7001"),
                      help="QLever port [QLEVER_PORT]")
    conn.add_argument("--access-token", default=os.environ.get("QLEVER_ACCESS_TOKEN"),
                      help="QLever access token [QLEVER_ACCESS_TOKEN]")
    conn.add_argument("--entity-data-url", default=os.environ.get("ENTITY_DATA_URL"),
                      help="base URL of Special:EntityData [ENTITY_DATA_URL]")
    conn.add_argument("--base-uri", default=os.environ.get("WIKIBASE_CONCEPT_URI"),
                      help="concept base URI, e.g. https://portal.mardi4nfdi.de "
                           "[WIKIBASE_CONCEPT_URI]")
    conn.add_argument("--api-url", default=os.environ.get("WIKIBASE_API_URL"),
                      help="MediaWiki api.php, needed for --changed-since "
                           "[WIKIBASE_API_URL]")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    log = lambda msg: print(msg, file=sys.stderr, flush=True)

    sources = [bool(args.ids), bool(args.from_file), bool(args.changed_since)]
    if sum(sources) != 1:
        log("resync: give entity ids, --from-file, or --changed-since (exactly one)")
        return 2
    if not args.entity_data_url:
        log("resync: --entity-data-url / ENTITY_DATA_URL is required")
        return 2
    if not args.base_uri:
        log("resync: --base-uri / WIKIBASE_CONCEPT_URI is required")
        return 2
    if not args.check and not args.access_token:
        log("resync: --access-token / QLEVER_ACCESS_TOKEN is required to write "
            "(use --check for a read-only run)")
        return 2

    entity_data_url = args.entity_data_url
    if not entity_data_url.endswith("/"):
        entity_data_url += "/"
    uris = Uris(args.base_uri)
    store = Store(args.host_name, args.port, args.access_token, args.timeout)

    try:
        if args.changed_since:
            api_url = args.api_url or _guess_api_url(entity_data_url)
            since = parse_since(args.changed_since)
            log(f"looking for entities changed since {since}")
            ids = changed_since(api_url, since,
                                [n.strip() for n in args.namespaces.split(",")],
                                args.limit, args.timeout, log)
        elif args.from_file:
            ids = read_ids(args.from_file)
        else:
            ids = args.ids
    except (HttpError, ValueError, OSError) as exc:
        log(f"resync: {exc}")
        return 2

    bad = [i for i in ids if not ID_RE.match(i)]
    if bad:
        log(f"resync: not entity ids: {' '.join(bad[:10])}")
        return 2
    if not ids:
        log("resync: nothing to do")
        return 0

    return reconcile_all(store, uris, entity_data_url, ids, args, log)


def _guess_api_url(entity_data_url):
    marker = "/wiki/Special:EntityData/"
    if marker in entity_data_url:
        return entity_data_url.split(marker)[0] + "/w/api.php"
    raise ValueError("cannot derive the API url; pass --api-url")


def reconcile_all(store, uris, entity_data_url, ids, args, log):
    verb = "checking" if args.check else "reconciling"
    log(f"{verb} {len(ids)} entit{'y' if len(ids) == 1 else 'ies'} "
        f"against {store.endpoint}")

    drifted = failed = missing = 0
    started = time.time()

    for index, eid in enumerate(ids, 1):
        try:
            target = fetch_target(entity_data_url, eid, uris, args.timeout)
            if target is None:
                missing += 1
                # A wiki that is down or misconfigured can 404 everything; do
                # not let that turn a bulk run into a mass deletion.
                if len(ids) >= 10 and missing > max(2, len(ids) // 5):
                    log(f"resync: aborting, {missing} of {index} entities are "
                        "missing upstream -- is the wiki healthy?")
                    return 2
            drift = diff(store, eid, uris, target, args.timeout)
        except (HttpError, ValueError) as exc:
            failed += 1
            print(f"{eid:14s} ERROR {exc}", flush=True)
            continue

        if drift.clean:
            if args.verbose or len(ids) == 1:
                print(f"{eid:14s} in sync", flush=True)
            continue

        drifted += 1
        note = " (deleted upstream)" if drift.missing_upstream else ""
        if drift.skipped_bnodes:
            note += f" [{drift.skipped_bnodes} blank-node triples skipped]"

        if args.check:
            print(f"{eid:14s} drift: {drift.summary()}{note}", flush=True)
            if args.print_sparql:
                for operation in build_operations(drift):
                    print(operation + "\n;", flush=True)
        else:
            try:
                store.update(build_operations(drift))
            except HttpError as exc:
                failed += 1
                drifted -= 1
                print(f"{eid:14s} ERROR applying update: {exc}", flush=True)
                continue
            print(f"{eid:14s} updated: {drift.summary()}{note}", flush=True)

        if args.verbose:
            for t in drift.deleted:
                print(f"    - {ser_triple(t)}"[:240], flush=True)
            for t in drift.inserted + drift.shared_missing:
                print(f"    + {ser_triple(t)}"[:240], flush=True)

    elapsed = time.time() - started
    log(f"done in {elapsed:.1f}s: {len(ids)} checked, {drifted} "
        f"{'drifted' if args.check else 'updated'}, {failed} failed"
        + (f", {missing} missing upstream" if missing else ""))

    if failed:
        return 2
    if args.check and drifted:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
