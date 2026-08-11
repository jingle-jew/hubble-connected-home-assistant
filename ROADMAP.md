# Roadmap

This roadmap describes planned work, not confirmed device compatibility.
State-changing camera commands are added only after their transport, accepted
values, acknowledgements, and resulting device state have been verified on an
owner-authorized camera.

## Hubble 3667 cloud controls

The verified 3667 firmware does not expose the MBP167 local HTTP command
service used by the 0667 and 1667 during normal operation. The integration
therefore currently exposes only the 3667 values confirmed through Hubble's
cloud `publish_command` job API: temperature, Wi-Fi strength, and current video
bitrate. Re-enabling the existing local entities would only recreate
unavailable controls.

Planned work:

- identify the official 3667 commands and attributes for night-vision mode,
  video bitrate, indicator LED, and ceiling-mount image flip;
- determine the corresponding read commands, accepted value ranges, response
  format, acknowledgement behavior, and persistence across a cold boot;
- validate each command independently through the account-authorized cloud
  command path without exposing credentials or device identifiers;
- implement a bounded cloud write API with explicit error handling and a
  post-command state refresh;
- expose dedicated Home Assistant `select` and `switch` entities backed by the
  cloud coordinator, without restoring unsupported local 3667 entities;
- add synthetic protocol tests, entity tests, diagnostics, translations, and
  documentation for the verified controls;
- confirm that the 0667 and 1667 continue using their existing local controls
  without regression.

Open questions:

- whether all four settings are writable through the same
  `/v1/devices/{registration_id}/publish_command.json` job API used by the
  verified getters;
- whether any setting instead requires Orbweb/P2P control traffic;
- whether supported values differ from the MBP167 values already used by the
  0667 and 1667;
- whether changes are persistent, session-scoped, or reset by the camera after
  reboot.
