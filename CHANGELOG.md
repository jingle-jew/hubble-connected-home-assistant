# Changelog

All notable changes will be documented here.

## Unreleased

- Log cloud sign-in outcomes, empty or incomplete camera inventories, and
  discovered cameras without a supported video route. The logs omit account
  credentials, tokens, and camera identifiers.

## 0.1.1-beta.2 - 2026-11-22

- Add an optional local IP address for each model 3667 camera during setup
  and in the integration options. Automatic MAC matching is available when
  the address is left blank.
- Group the model 3667 video, sensors, and controls under one Home Assistant
  device. Migrate existing matched devices while preserving their local camera
  entity ID.

## 0.1.0-beta.2 - 2026-11-22

- Add manual setup and local Orbweb video and audio for Motorola VM85CONNECT.
- Add HubbleClub for Partners sign-in and live video for Luvion Essential Connect
  (model 1844), using a shared stream for multiple viewers.
- Improve cloud discovery for model 3667.
- Read the 3667 temperature, Wi-Fi strength, and video bitrate locally through
  the authenticated Orbweb mapping instead of cloud `publish_command` jobs.
- Read 3667 brightness, night-vision mode, and ceiling-mount state through the
  same local Orbweb command mapping, and add bounded local controls for those
  settings plus video bitrate.
- Do not expose 3667 contrast entities: its local `get_contrast` command returns
  HTTP 204 and the official app excludes contrast for this model family.
- Add verified image-brightness and image-contrast sensors and controls for
  compatible local cameras.
- Suppress image controls on the 0667, where changing an image level terminates
  the direct RTSP service.
- Document installation through the Home Assistant Marketplace.

Thanks to @twoenter for the HubbleClub for Partners and Motorola Nursery pull
requests.

## 0.1.0-beta.1 - 2026-08-09

- Add native same-LAN Orbweb video without Android or Java.
- Add stable loopback RTSP endpoints with renewable encrypted transports.
- Add direct RTSP discovery for compatible legacy cameras.
- Add temperature, connectivity, Wi-Fi, and bitrate sensors.
- Add verified night-vision, bitrate, indicator-LED, and image-flip controls.
- Redact camera and account identifiers from diagnostics.
