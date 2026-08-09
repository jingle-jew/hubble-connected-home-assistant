# Clean-room protocol notes

This document describes the interoperability boundary implemented by the
integration. It intentionally omits account data, device identifiers, network
addresses, captured payloads, and proprietary application code.

## Evidence policy

Protocol behavior is classified as one of:

- **observed**: visible in network traffic produced by an owned device and an
  authorized account;
- **corroborated**: independently reproduced by the clean-room implementation;
- **hypothesis**: not used by the runtime until verified.

Tests contain only synthetic identities, locally administered MAC addresses,
documentation-only IP addresses, and in-memory byte streams. The repository
does not distribute vendor applications, Java archives, decompiled source, or
packet captures.

## Authentication and ownership boundary

The runtime follows the same trust boundaries as the official client:

1. authenticate the user's account against the Hubble API;
2. request the inventory of devices owned by that account;
3. obtain the selected camera's Orbweb session identifier and password;
4. request Orbweb rendezvous candidates;
5. negotiate encrypted tunnel key material;
6. authenticate the mapped camera control service with the per-camera password;
7. carry RTSP only after those steps succeed.

Incorrect account credentials, mismatched identities, malformed responses, and
camera authentication failures are rejected. Generated client identifiers are
installation-scoped protocol values, not substitutes for account or camera
credentials.

## Network map

| Boundary | Purpose | Runtime status |
| --- | --- | --- |
| `api.hubble.in` over HTTPS | account authentication and owned-device inventory | enabled |
| `rdz.orbwebsys.com` over HTTPS | Orbweb rendezvous discovery | enabled |
| returned rendezvous host over TCP | client registration and direct-session signaling | enabled |
| camera high TCP port | encrypted same-LAN Orbweb tunnel | enabled |
| camera ports 9001 and 6667 inside the tunnel | camera authentication and RTSP | enabled |
| NAT/shunt/relay services | off-LAN fallback | parsed and tested in isolation, disabled at runtime |

Hubble also documents RTMP, HTTP command/file transfer, STUN, and high UDP P2P
ports for portions of its product family. Their presence does not imply that a
specific model exposes RTSP or uses every transport.

## Media boundary

The verified camera-side media service produces RTSP carrying H.264 video and,
on tested models, G.711 A-law audio. Model and firmware behavior varies. The
integration therefore probes the actual endpoint and does not assume port 554
or ONVIF support.

Orbweb tunnel lifetimes can be shorter than a Home Assistant player session.
The integration exposes a stable loopback RTSP port per camera and replaces the
encrypted transport behind it. Consumers reconnect to the same local URL rather
than retaining an expired dynamic mapping.

## Defensive implementation rules

- All binary lengths, field widths, roles, ports, identities, and operation
  sequences are bounded and validated.
- Secret fields are excluded from representations, logs, diagnostics, and test
  fixtures.
- Packet decoders report structure only; they never print application bodies.
- Normal setup does not run the experimental NAT, shunt, or relay paths.
- Local discovery reads the existing ARP table and verifies candidates; it does
  not sweep the subnet.
- State-changing camera commands are exposed only when their exact value ranges
  were verified and are initiated explicitly by the Home Assistant user.

## Compatibility expectations

The cloud API and Orbweb protocol are undocumented vendor interfaces and may
change. A failure should be classified at the cloud, rendezvous, tunnel,
authentication, RTSP, or media boundary without logging the associated secret.
Compatibility reports must use Home Assistant diagnostics and must never
include raw captures or config-entry storage.
