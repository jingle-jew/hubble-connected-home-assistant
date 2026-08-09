# Hubble Connected for Home Assistant

Unofficial Home Assistant integration for owner-authorized Hubble Connected and
Motorola baby cameras.

> [!WARNING]
> This project is in beta. Device and firmware coverage is incomplete, and the
> cloud-assisted Orbweb path may change without notice. Keep the official app
> available while evaluating the integration.

This project is independent of Hubble Connected, Motorola Mobility, Binatone,
and Orbweb. Product names are used only to identify compatible devices.

## What it provides

- Home Assistant camera entities with video and audio;
- a clean-room Orbweb client for same-LAN cameras that do not expose RTSP
  directly;
- direct RTSP support for compatible legacy models;
- temperature, connectivity, Wi-Fi strength, and video bitrate sensors when
  the camera exposes them;
- verified controls for night vision, video bitrate, indicator LED, and image
  flip.

No Android phone, Java browser plugin, separate bridge, camera exploit, firmware
change, or account-authentication bypass is required.

## How video reaches Home Assistant

```text
Hubble account -- owned-device inventory --> Home Assistant
                                               |
Orbweb rendezvous <-- authenticated session ---+
        |
        +---- direct encrypted LAN tunnel ---- camera
                                                 |
Home Assistant stream/go2rtc <-- stable RTSP proxy+
```

The account is used to obtain the identifiers and per-camera credentials that
the official client normally uses. The integration then negotiates an encrypted
Orbweb session and authenticates to the owned camera before carrying RTSP over
that session. It does not guess credentials or weaken camera access controls.

Some legacy models expose `RTSP` directly on the LAN. That path is detected with
a bounded `OPTIONS` request and does not require Hubble cloud access.

## Requirements

- Home Assistant 2026.8 or newer;
- Home Assistant and the cameras on the same trusted LAN for Orbweb LAN video;
- a valid Hubble account containing the cameras that require Orbweb;
- outbound HTTPS access to the official Hubble and Orbweb rendezvous services.

Only a small number of models and firmware versions have been tested. Please
open a compatibility report rather than assuming all Hubble-branded devices use
the same protocol.

## Confirmed compatible models

- 0667
- 1667
- 3667

These model families have been verified on user-owned cameras. Compatibility
can still vary by hardware revision, region, and firmware version.

## Installation

### HACS custom repository

To install the integration with HACS:

1. Open **HACS > Integrations > Custom repositories**.
2. Add `https://github.com/jingle-jew/hubble-connected-home-assistant` as an
   **Integration** repository.
3. Install **Hubble Connected** and restart Home Assistant.
4. Open **Settings > Devices & services > Add integration** and select
   **Hubble Connected**.

### Manual

Copy `custom_components/hubble_connected` to
`<home-assistant-config>/custom_components/hubble_connected`, restart Home
Assistant, and add the integration from **Settings > Devices & services**.

## Configuration

Enter the email address and password used by the official Hubble application.
They are sent only to the official Hubble API and are stored in Home Assistant's
config-entry storage so the integration can reauthenticate after a restart.
Protect the Home Assistant configuration directory and its backups.

The optional local camera list is useful when a legacy camera is missing from
the cloud inventory. Its format is:

```text
Nursery=<camera-ip>, Office=<camera-ip>
```

The integration first matches owned cloud devices against complete entries in
Home Assistant's ARP table. It verifies every candidate with a read-only Hubble
getter and does not scan an entire subnet.

## Privacy and network use

The integration communicates with:

- `api.hubble.in` for account authentication and owned-device inventory;
- `rdz.orbwebsys.com` and returned Orbweb rendezvous hosts to establish a
  camera session;
- the cameras directly on the local network;
- loopback-only RTSP proxy ports inside the Home Assistant host.

It sends no analytics or camera data to this project's maintainer. Credentials,
camera identifiers, MAC addresses, LAN addresses, RTSP URLs, and names are
excluded from integration diagnostics. Never attach raw packet captures, Home
Assistant `.storage` files, or unredacted logs to a public issue.

See [SECURITY.md](SECURITY.md) before reporting a security problem.

## Known limitations

- The Orbweb implementation currently supports the verified direct-LAN TCP
  route. Internet relay fallback is not enabled.
- Tunnel renewal is still being tested across more models and firmware.
- PTZ, talkback, motion events, and cloud snapshots are not exposed.
- Temperature units are handled by Home Assistant; no camera-side Celsius /
  Fahrenheit setting has been verified.
- Cloud or firmware changes may require an integration update.

## Development

The runtime implementation is under `custom_components/hubble_connected`.
Protocol tests use synthetic identities, documentation-only addresses, and
in-memory transports. `tools/orbweb_packet_decoder.py` reports only anonymous
packet structure and never emits captured payloads.

Before contributing, read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[clean-room protocol notes](docs/protocol.md). Contributions must not contain
vendor binaries, decompiled source, packet captures, real device identifiers,
or credentials.

Run the local checks with:

```sh
ruff check custom_components tests tools
python -m unittest discover -s tests -v
```

## License

The independently written source code is available under the [MIT License](LICENSE).
Use it only with devices and accounts you are authorized to access and in
accordance with the laws and agreements applicable to you.
