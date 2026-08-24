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

The RPC API on port 5001 is not included in the Service and therefore has no
public route.

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
