# Hubble Connected for Home Assistant

Unofficial Home Assistant integration for owner-authorized Hubble Connected,
Luvion, and Motorola baby cameras.

> [!WARNING]
> This project is in beta. Device and firmware coverage is incomplete, and the
> cloud-assisted Orbweb path may change without notice. Keep the official app
> available while evaluating the integration.

This project is independent of Hubble Connected, Motorola Mobility, Binatone,
and Orbweb. Product names are used only to identify compatible devices.

## What it provides

- Home Assistant camera entities with video and audio;
- automatic fallback from the standard Hubble cloud profile to the Hubble
  partner profile used by compatible accounts like HubbleClub for Partners;
- a clean-room Orbweb client for same-LAN cameras that do not expose RTSP
  directly;
- direct RTSP support for compatible legacy models;
- temperature, connectivity, Wi-Fi strength, and video bitrate sensors when
  the camera exposes them;
- verified controls for night vision, video bitrate, image brightness, image
  contrast, indicator LED, and image flip when the camera exposes them.

No Android phone, Java browser plugin, or separate bridge is required during
normal operation. Manual VM85CONNECT onboarding currently uses the official
Android app once to obtain the owner's camera credentials. No camera exploit,
firmware change, or account-authentication bypass is used.

## How video reaches Home Assistant

| Setup path | How ownership credentials are obtained | Media path |
| --- | --- | --- |
| Standard Hubble account | Hubble cloud inventory | Authenticated Orbweb LAN tunnel or verified direct RTSP |
| HubbleClub for Partners account | Automatic partner-profile fallback after standard authentication is rejected | Authenticated Orbweb LAN tunnel, with a shared Home Assistant go2rtc restream for model 1844 |
| Motorola Nursery | One-time extraction from the owner's authorized Motorola Nursery Android app session | Authenticated Orbweb LAN tunnel to camera port 6667 and `/blinkhd` |

For cloud-provisioned cameras, the account is used to obtain the identifiers
and per-camera credentials that the official client normally uses. For a manual
VM85 entry, the owner supplies the equivalent camera-specific values through
the config flow. The integration then negotiates an encrypted Orbweb session
and authenticates to the owned camera before carrying RTSP over that session.

On the verified 3667, the same session also carries bounded HTTP getter and
control requests from the camera's mapped port 80. The integration does not
guess credentials, weaken camera access controls, or provide unauthenticated
access to a camera.

Some legacy models expose `RTSP` directly on the LAN. That path is detected with
a bounded `OPTIONS` request and does not require Hubble cloud access.

**After setup, the official mobile app is not part of normal Home Assistant
operation.** Cameras with direct local RTSP support operate entirely over the
LAN and do not require Hubble cloud access for video. Cameras using the Orbweb
path still require the applicable ownership credentials and Orbweb rendezvous
services to authenticate and establish the session, but the actual video is
carried directly over the local network.

Uninstalling the mobile app does not remove cameras from the Hubble account.
Removing a camera from the account may prevent Orbweb-based cameras from working
with this integration.

## Requirements

- Home Assistant 2026.8 or newer;
- Home Assistant and the cameras on the same trusted LAN;
- for cloud setup: a valid Hubble Connected or compatible Hubble partner
  account containing the camera;
- for manual VM85CONNECT onboarding only: the official Motorola Nursery
  Android app with a working owner session, USB debugging, and Android
  platform-tools on a Windows computer;
- outbound HTTPS access to the official Hubble and Orbweb rendezvous services.

Only a small number of models and firmware versions have been tested. Please
open a compatibility report rather than assuming all Hubble-branded devices use
the same protocol.

## Confirmed compatible models

| Model | Verified setup | Notable behavior |
| --- | --- | --- |
| 0667 | Hubble account or direct LAN discovery | Image-level controls are suppressed because they can stop RTSP on tested firmware |
| 1667 | Hubble account or direct LAN discovery | Uses the existing Hubble/local path |
| 1844 Luvion Essential Connect | Hubble partner account | Supports automatic authentication-profile fallback and uses a shared go2rtc restream |
| 3667 | Hubble account | Supports verified local telemetry and controls through the Orbweb mapping |
| Motorola VM85CONNECT | Manual owner-authorized setup | Uses model-specific Orbweb and RTSP credentials; hardware 1.2.03 and firmware 1.6.45 tested |

The detailed table covers video paths, sensors, and controls for each model.
[Compatible devices](COMPATIBLE_DEVICES.md)

These model families have been verified on user-owned cameras. Compatibility
can still vary by hardware revision, region, and firmware version.

To report results for another camera model, open a GitHub issue with the model,
firmware version, and redacted diagnostics.

<!-- ha-marketplace:exclude-start -->
## Installation

### Home Assistant Marketplace (2026.11 and later)

1. Open **Settings > Marketplace** and select **Add from link**.
2. Enter `https://github.com/jingle-jew/hubble-connected-home-assistant` and
   connect GitHub if prompted. If asked for a type, choose **Integration**.
3. Open **Hubble Connected** in the Marketplace and choose **Install**.
   Beta releases require the repository's **Pre-release** switch.

### HACS on earlier Home Assistant versions

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
<!-- ha-marketplace:exclude-end -->

## Configuration

Choose **Hubble Connected account** for the existing cloud-assisted setup, or
**Motorola VM85CONNECT (manual)** for an owner-provisioned VM85 camera. Existing
config entries created before this choice was added continue to use the Hubble
provider.

### Hubble Connected account

Enter the email address and password used by the applicable official Hubble
application. They are sent only to the official Hubble API and are stored in
Home Assistant's config-entry storage so the integration can reauthenticate
after a restart. Protect the Home Assistant configuration directory and its
backups.

Authentication first uses the standard Hubble request profile. If the login
endpoint returns HTTP 401, 403, or 424, the integration automatically retries
with the Hubble partner tenant and application headers. The successful profile remains attached
to that cloud session for inventory and camera requests. Users do not need to
select or enter these headers manually.

The optional local camera list is useful when a legacy camera is missing from
the cloud inventory. Its format is:

```text
Nursery=<camera-ip>, Office=<camera-ip>
```

Some owned cameras, including the verified 3667 firmware, can remain absent
from the account's `devices/own` list while still appearing in the account's
subscription inventory. The integration automatically recovers those 3667
identifiers, validates their individual v6 profiles, retrieves their Orbweb
credentials, and polls temperature, Wi-Fi strength, and video bitrate from the
camera's HTTP command service through the encrypted same-LAN Orbweb mapping.
The cloud API is not used to publish those getter commands. **Cloud camera
identifiers omitted from inventory** remains available in the integration
options as a manual fallback. Identifiers are excluded from diagnostics.

During setup or in the integration options, you can enter the LAN IP for each
detected 3667. Leave it blank to use automatic MAC matching. Linked video,
sensors, and controls appear under one Home Assistant device.

The integration first matches owned cloud devices against complete entries in
Home Assistant's ARP table by exact MAC address. Those authenticated cloud
matches remain eligible for Orbweb video even when the optional local HTTP
getter is unavailable. ARP neighbors absent from the cloud inventory are still
accepted only when a read-only Hubble getter confirms the same MAC address. The
integration does not scan an entire subnet.

### Motorola VM85CONNECT (manual)

The manual VM85 flow is intended for owners who can view their own camera in
the official Motorola Nursery Android app. It asks for:

- the camera name and private LAN IPv4 address;
- its device-specific Orbweb SID and Orbweb password;
- the Orbweb account name used by the camera;
- its RTSP username and password.

The verified profile uses an owner-authenticated Orbweb tunnel to camera port
6667 and requests `/blinkhd`. The stream contains H.264 video and G.711 A-law
audio. Hardware version 1.2.03 with firmware 1.6.45 has been tested.

The camera must be on the same LAN as Home Assistant. Connection details are
stored in Home Assistant config-entry storage and are never included in
diagnostics. Protect the Home Assistant configuration directory and backups,
and never post these values in an issue or pull request. Automatic retrieval
through a Motorola/5GenCare account is outside the scope of this manual flow.

Owners can retrieve the connection details with
[`extract_vm85_credentials.ps1`](tools/extract_vm85_credentials.ps1).
Extract Android platform-tools (ADB), enable USB debugging, connect and authorize the
phone, close any private material visible in the terminal, and run this from
Windows PowerShell at the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\extract_vm85_credentials.ps1 `
  -AdbPath C:\platform-tools\adb.exe
```

Close Motorola Nursery before starting the helper so a new connection is
logged. When prompted, open the app and view only the target VM85CONNECT. Wait
for video, return to PowerShell, and press Enter. If the app log does not reveal the local address, add for example
`-CameraIp 192.168.1.50` or copy the camera address from the router.

The helper prints the camera LAN address, Orbweb SID, Orbweb account, Orbweb
device password, RTSP username, and RTSP password. It reads the complete
`logcat` output in memory and does not create a raw log file. Treat its output
and the optional clipboard contents as camera credentials. Do not share them in
screenshots, issues, pull requests, or diagnostics. The helper depends on
diagnostic messages emitted by the currently tested Motorola Nursery Android
app and may need updating if the app changes.

Automatic Motorola/5GenCare account login and email-OTP handling are not
implemented. A temporary camera share code is not a replacement for the
camera-specific owner credentials used by this flow.

## Troubleshooting

### Hubble authentication reports HTTP 424

No manual header change should be necessary. HTTP 424 from the authentication
endpoint is treated as rejection of the standard request profile, after which
the integration retries with the Hubble partner profile. If setup still fails,
verify the credentials in the official app and enable debug logging without
publishing the password, authentication token, or camera identifiers.

### The VM85 helper cannot find all values

Close Motorola Nursery and run the helper again. Open the app and one
camera's live view when PowerShell prompts you. Wait until video
is visible before pressing Enter. The phone must be authorized in `adb devices`.
If only the LAN address is missing, obtain it from the router or pass
`-CameraIp` explicitly.

### A stream takes time to start

The tested VM85CONNECT starts slowly. Allow the initial
Orbweb mapping and RTSP negotiation to complete before retrying. Repeatedly
opening competing viewers during startup can delay the first frame.

## Privacy and network use

The integration communicates with:

- `api.hubble.in` for standard or partner-profile account authentication and
  owned-device inventory for cloud-configured cameras;
- `rdz.orbwebsys.com` and returned Orbweb rendezvous hosts to establish a
  camera session;
- the cameras directly on the local network;
- loopback-only RTSP and command proxy ports inside the Home Assistant host.

It sends no analytics or camera data to this project's maintainer. Credentials,
camera identifiers, MAC addresses, LAN addresses, RTSP URLs, and names are
excluded from integration diagnostics. Never attach raw packet captures, Home
Assistant `.storage` files, or unredacted logs to a public issue.

See [SECURITY.md](SECURITY.md) before reporting a security problem.

## Known limitations

- The Orbweb implementation currently supports the verified direct-LAN TCP
  route. Internet relay fallback is not enabled.
- Image brightness and contrast entities are suppressed on the 0667 because
  changing those values terminates its direct RTSP encoder and the tested
  firmware does not restart the service safely.
- The 3667 exposes local brightness, bitrate, night-vision, and ceiling-mount
  state through Orbweb. Its contrast getter returns HTTP 204, so contrast is not
  exposed for this model.
- The 1844 uses a shared Home Assistant-managed go2rtc restream. This prevents
  multiple viewers from opening competing camera-side Orbweb streams. The
  standard cloud profile may return HTTP 424; partner fallback is automatic.
- The VM85CONNECT can be added through the advanced manual setup when its
  device-specific Orbweb SID and password have been obtained from the owner's
  Motorola Nursery app session. It uses an authenticated same-LAN Orbweb tunnel
  to RTSP port 6667 and the `/blinkhd` H.264 stream. Automatic Motorola/5GenCare
  account discovery is not implemented yet.
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
