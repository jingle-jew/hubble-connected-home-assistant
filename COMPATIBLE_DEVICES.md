# Compatible devices

These cameras have been tested with Hubble Connected for Home Assistant.
Hardware revision, region, and firmware can affect which features are available.

## Tested cameras

| Camera | Setup | Local video | Local sensors | Local controls | Cloud sensors |
| --- | --- | --- | --- | --- | --- |
| `0667` | Hubble account or LAN discovery | Direct RTSP | Temperature, Wi-Fi strength and connectivity, current video bitrate | Night vision, video bitrate, indicator LED, ceiling mount | Unverified |
| `1667` (MBP167) | Hubble account or LAN discovery | Direct RTSP | Temperature, Wi-Fi strength and connectivity, current video bitrate, image brightness and contrast | Night vision, video bitrate, indicator LED, ceiling mount, image brightness and contrast | Unverified |
| `1844` (Luvion Essential Connect) | HubbleClub for Partners | Orbweb LAN with a shared go2rtc stream for multiple viewers | Unverified | Unverified | Unverified |
| `3667` | Hubble account with optional LAN IP | Orbweb LAN with H.264 video | Temperature, Wi-Fi strength, current video bitrate, image brightness | Night vision, video bitrate, image brightness, ceiling mount | Temperature, Wi-Fi strength, current video bitrate via cloud fallback |
| Motorola VM85CONNECT | Manual setup with Motorola Nursery camera credentials | Orbweb LAN with H.264 video and G.711 A-law audio | — | — | — |

The current VM85CONNECT setup exposes a camera entity only.

The `0667` and `1667` read sensors and send commands over direct LAN HTTP.
Individual entities depend on the getters exposed by the camera firmware. On
the tested `0667`, changing image brightness or contrast stops its RTSP encoder,
so the integration suppresses those entities.

The `3667` reads sensors and sends commands through the same Orbweb LAN tunnel
used for video. Home Assistant groups the stream, sensors, and controls on one
device. The contrast getter returns HTTP `204` on the tested firmware.
[Release notes](CHANGELOG.md)

The `1844` stream was tested with concurrent viewers, repeated reopening, and
reconnection. The VM85CONNECT test used hardware `1.2.03` and firmware `1.6.45`.
[Luvion test report](https://github.com/jingle-jew/hubble-connected-home-assistant/pull/1), [Motorola test report](https://github.com/jingle-jew/hubble-connected-home-assistant/pull/2)

## Cloud and LAN behavior

All video paths in the table carry media over the local network. A Hubble
account provides camera discovery and, when available, Orbweb credentials.
Orbweb uses an internet rendezvous service to establish the LAN tunnel. Direct
RTSP and direct LAN HTTP can run without a Hubble account.

For a `3667` without Orbweb credentials, the integration can request
temperature, Wi-Fi strength, and current video bitrate through the Hubble cloud
API. This fallback is also available for cameras recovered by registration ID,
provided the cloud API supports those readings for the camera.

To report compatibility for another camera or firmware version, include the
model, firmware, working entities, and redacted diagnostics.
[Diagnostics guidance](README.md#privacy-and-network-use) · [Open a GitHub issue](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/new/choose)

## Motorola credential helper coverage

The [Python credential helper](tools/extract_vm85_credentials.py) targets the
Motorola Nursery Android app's VM85CONNECT diagnostic format. It runs with
Python 3.10+ and ADB without third-party Python packages. Synthetic extraction
and ADB workflow tests run on Linux; physical phone/camera validation and
execution on macOS and Windows remain outstanding. The existing Windows
PowerShell helper is retained unchanged. These helper checks do not add new
hardware to the tested camera table above.

VM65X Connect is **not verified** for this helper or the VM85 manual profile.
[Issue #3](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/3)
reports email-OTP login in another app (`com.hubble.care`), not a successful
Motorola Nursery credential capture. Hardware, firmware, app, and log-format
compatibility must be established separately from cloud login support.

[Installation, privacy, and troubleshooting](README.md#install-python-and-adb)
