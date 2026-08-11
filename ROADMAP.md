# Roadmap

This roadmap describes planned work, not confirmed device compatibility.
State-changing camera commands are added only after their transport, accepted
values, acknowledgements, and resulting device state have been verified on an
owner-authorized camera.

## Hubble 3667 controls

The verified 3667 firmware does not expose the MBP167 HTTP command service
directly on its LAN address. The integration maps camera port 80 through the
authenticated Orbweb LAN tunnel and reads temperature, Wi-Fi strength, and
current video bitrate locally. The 0667 and 1667 continue to use their existing
direct LAN HTTP transport.

Planned work:

- identify the official 3667 commands and attributes for night-vision mode,
  video bitrate, indicator LED, and ceiling-mount image flip;
- determine the accepted write values, response format, acknowledgement
  behavior, and persistence across a cold boot;
- validate each write independently through an owner-authorized path without
  exposing credentials or device identifiers;
- implement a bounded write API with explicit error handling and a
  post-command state refresh;
- expose dedicated Home Assistant `select` and `switch` entities backed by the
  appropriate 3667 coordinator, without restoring unsupported direct-LAN 3667
  entities;
- add synthetic protocol tests, entity tests, diagnostics, translations, and
  documentation for the verified controls;
- confirm that the 0667 and 1667 continue using their existing local controls
  without regression.

Open questions:

- whether all four settings are writable through the mapped HTTP service or
  require a distinct cloud or Orbweb/P2P control channel;
- whether supported values differ from the MBP167 values already used by the
  0667 and 1667;
- whether changes are persistent, session-scoped, or reset by the camera after
  reboot.
