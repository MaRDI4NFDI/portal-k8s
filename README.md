# Kubernetes deployment

Kubernetes repository for the MaRDI portal

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
