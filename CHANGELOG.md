# Changelog

All notable changes will be documented here.

## 0.1.0-beta.1 - Unreleased

- Add native same-LAN Orbweb video without Android or Java.
- Add stable loopback RTSP endpoints with renewable encrypted transports.
- Add direct RTSP discovery for compatible legacy cameras.
- Add temperature, connectivity, Wi-Fi, and bitrate sensors.
- Read the 3667 temperature, Wi-Fi strength, and video bitrate locally through
  the authenticated Orbweb mapping instead of cloud `publish_command` jobs.
- Add verified night-vision, bitrate, indicator-LED, and image-flip controls.
- Add verified image-brightness and image-contrast sensors and controls for
  compatible local cameras.
- Suppress image controls on the 0667, where changing an image level terminates
  the direct RTSP service.
- Redact camera and account identifiers from diagnostics.
