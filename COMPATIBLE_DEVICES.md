# Compatible devices

Find your camera's setup method, video path, sensors and controls in the
[devices and features guide](https://github.com/jingle-jew/hubble-connected-home-assistant/wiki/Devices-and-features).
It covers the tested 0667, 1667 (MBP167), Luvion Essential Connect 1844,
3667 and Motorola VM85CONNECT. Check the model and firmware when comparing results.

## HubbleClub 7101

GodofGTASA tested firmware `03.60.32` with the registration ID field blank.
Home Assistant discovered the camera through the Hubble account's subscription
list and played live video over Orbweb on the same LAN. The local sensor and
control entities were unavailable in this setup. The cloud inventory returned
no temperature or Wi-Fi strength.
[Hardware report](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/4#issuecomment-7111737685) · [Sensor and control tracking](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/6)

## Hubble 0864 (experimental)

For automatic discovery from the subscription list, sign in with the Hubble
account and leave the optional registration ID field blank. Simulated account
responses exercise discovery while hardware validation is pending. Home
Assistant creates a camera entity when it finds a supported video route.
Live video, sensors, and controls are unverified on this model.
[Hardware validation tracking](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/8)

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

[Installation, privacy, and troubleshooting](docs/vm85-credentials.md#install-python-and-adb)

## Report a model

For a new model or firmware, describe which features worked on the physical
camera and include reviewed diagnostics if available.
[Diagnostics guide](https://github.com/jingle-jew/hubble-connected-home-assistant/wiki/Troubleshooting-and-diagnostics) · [Report a model](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/new/choose)
