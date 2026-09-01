# IPFS

This chart runs one Kubo node using the standard `ipfs/kubo` image. The image
tag is intentionally omitted, matching the previous Compose deployment. See
the [Kubo container documentation](https://docs.ipfs.tech/install/run-ipfs-inside-docker/)
for the image paths and ports used below.

Only settings that differ from the Kubo or Kubernetes defaults are specified:

- `IPFS_PROFILE=server` keeps the server profile used by the Compose deployment;
  this is the [recommended profile for a public server](https://docs.ipfs.tech/how-to/command-line-quick-start/#initialize-the-repository).
- The [`unixfs-v1-2025` CID profile](https://specs.ipfs.tech/ipips/ipip-0499/)
  makes new UnixFS imports use the deterministic CIDv1 settings defined by
  IPIP-0499. Existing CIDv0 content and pins remain valid.
- `/data/ipfs` is stored on a 20 GiB `csi-rbd-sc` volume. The old data is at
  most 10 GB, so this leaves room for the initial import.
- [`Addresses.AppendAnnounce`](https://docs.ipfs.tech/how-to/nat-configuration/#update-the-kubo-configuration)
  publishes the address through which other IPFS nodes can reach this node
  behind Traefik.
- Traefik sends HTTPS gateway requests to port 8080. It sends IPFS TCP and QUIC
  traffic from its port 4001 entrypoints to the corresponding Service ports.

The RPC API listens only on the pod's loopback interface and is not included
directly in the Service, so it has no public route.

## Authenticated WebUI

The optional admin endpoint places an OAuth2 Proxy sidecar in front of the
complete Kubo RPC/WebUI origin. This lets an allow-listed Google account use
`https://ipfs-admin.portal.mardi4nfdi.de/webui` while keeping port 5001 private.
Every allow-listed account receives full Kubo administrative access; there are
no per-user permissions.

In the [Google Auth Platform Clients](https://support.google.com/cloud/answer/15549257),
create a **Web application** OAuth client. Keep the app in testing mode, add the
administrators as test users, and configure this exact authorized redirect URI:

```text
https://ipfs-admin.portal.mardi4nfdi.de/oauth2/callback
```

Google displays the client ID and client secret when the client is created. Save
the downloaded JSON then because Google does not display new client secrets
again.

Install `sops`, `gnupg`, and `jq`, then run the helper from the repository root:

```console
brew install sops gnupg jq
charts/ipfs/create-production-secret.sh ~/Downloads/client_secret_....json
```

The helper verifies the redirect URI, asks for the allowed Google email address,
generates the cookie secret, imports `.sops.pub.asc`, and writes the encrypted
Secret to `apps/production/ipfs/secrets.yaml`. It never prints the Google client
secret. Add the encrypted Secret to `apps/production/ipfs/kustomization.yaml`:

```yaml
resources:
  - ../../base/ipfs
  - secrets.yaml
```

Commit the encrypted Secret and Kustomization change together. Flux decrypts
the Secret when reconciling production. See the MaRDI
[Secrets-K8s documentation](https://portal.mardi4nfdi.de/wiki/Project:Secrets-K8s)
for the repository-wide secret-management workflow.

Do not set a wildcard email domain: access is granted exclusively through the
Secret's `allowed-emails` file. Changing that file or the OAuth credentials
requires restarting the StatefulSet so OAuth2 Proxy reloads the Secret.

To add the initial data, forward the RPC port to an administrator's machine:

```console
kubectl -n production port-forward statefulset/ipfs 5001:5001
```

In the directory containing the source files, use a local Kubo CLI:

```console
ipfs --api=/ip4/127.0.0.1/tcp/5001 add -r *
ipfs --api=/ip4/127.0.0.1/tcp/5001 pin ls --type=recursive
```

Record the returned root CIDs before ending the port-forward session.

The `unixfs-v1-2025` profile requires Kubo 0.40 or newer. Confirm the running
version and active import configuration with:

```console
ipfs --api=/ip4/127.0.0.1/tcp/5001 version
ipfs --api=/ip4/127.0.0.1/tcp/5001 config Import
```
