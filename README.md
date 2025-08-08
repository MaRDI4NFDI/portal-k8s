# MaRDI Kubernetes Platform

Kubernetes repository for the MaRDI portal

## Architecture

### Cluster Structure

The platform uses a single cluster with two environments deployed in different namespaces:

<table>
    <tr>
        <th>Environment</th>
        <th>Purpose</th>
    </tr>
    <tr>
        <td>Production</td>
        <td>Live workloads</td>
    </tr>
    <tr>
        <td>Staging</td>
        <td>Pre-production testing</td>
    </tr>
</table>

### Repository Structure
```
├── apps/                   # Application deployments
│   ├── base/               # Base Helm releases and configurations
│   ├── production/         # Production environment overlays
│   └── staging/            # Staging environment overlays
├── charts/                 # Custom Helm charts
├── clusters/               # Cluster-specific Flux configuration
└── infrastructure/         # Core infrastructure components
```

## Applications

### Core Applications

<table>
    <tr>
        <th>Name</th>
        <th>Description</th>
    </tr>
    <tr>
        <td><a href="https://github.com/MaRDI4NFDI/docker-wikibase">Mediawiki/Wikibase</a></td>
        <td>Knowledge base collaborative platform</td>
    </tr>
    <tr>
        <td><a href="https://mariadb.org/">MariaDB</a></td>
        <td>Relational database</td>
    </tr>
    <tr>
        <td>WDQS</td>
        <td>Blazegraph database + SPARQL query endpoint</td>
    </tr>
    <tr>
        <td><a href="https://www.elastic.co/elasticsearch/">Elasticsearch</a></td>
        <td>Search engine</td>
    </tr>
</table>

### Supporting Services

<table>
    <tr>
        <th>Name</th>
        <th>Description</th>
    </tr>
    <tr>
        <td>Importer</td>
        <td>Data import service</td>
    </tr>
    <tr>
        <td>Job Runner</td>
        <td>Background job processor</td>
    </tr>
    <tr>
        <td>LaTeXML</td>
        <td>LaTeX to XML converter</td>
    </tr>
    <tr>
        <td>QuickStatements</td>
        <td>Wikibase editing tool</td>
    </tr>
    <tr>
        <td>S3 Proxy</td>
        <td>Object storage proxy</td>
    </tr>
    <tr>
        <td>FFmpeg</td>
        <td>Media processing service</td>
    </tr>
    <tr>
        <td><a href="https://redis.io/">Redis</a></td>
        <td>In-memory data store</td>
    </tr>
</table>

### Infrastructure Components

<table>
    <tr>
        <th>Component</th>
        <th>Description</th>
        <th>Purpose</th>
    </tr>
    <tr>
        <td><a href="https://matomo.org/">Matomo</a></td>
        <td>Web analytics platform</td>
        <td>Privacy-focused analytics</td>
    </tr>
    <tr>
        <td><a href="https://www.portainer.io/">Portainer</a></td>
        <td>Container management UI</td>
        <td>Kubernetes dashboard</td>
    </tr>
    <tr>
        <td><a href="https://traefik.io/">Traefik</a></td>
        <td>Cloud-native application proxy</td>
        <td>Ingress controller and load balancer</td>
    </tr>
    <tr>
        <td><a href="https://cert-manager.io/">Cert Manager</a></td>
        <td>X.509 certificate management</td>
        <td>Automatic TLS certificate provisioning</td>
    </tr>
    <tr>
        <td><a href="https://github.com/mariadb-operator/mariadb-operator">MariaDB Operator</a></td>
        <td>MariaDB Kubernetes operator</td>
        <td>Database lifecycle management</td>
    </tr>
    <tr>
        <td><a href="https://fluxcd.io/">Flux CD</a></td>
        <td>GitOps continuous delivery</td>
        <td>Automated deployments from Git</td>
    </tr>
</table>

## Documentation

- [Helm package manager](https://helm.sh/)
- [Flux (CD)](https://fluxcd.io/)

## Development Setup

### Prerequisites
- [uv](https://docs.astral.sh/uv/) - Fast Python package installer
- [SOPS](https://github.com/mozilla/sops) - For secret encryption

#### Create and activate virtual environment
```bash
uv venv .venv
source .venv/bin/activate
```

#### Install pre-commit hooks
```bash
pip install pre-commit
pre-commit install
```
