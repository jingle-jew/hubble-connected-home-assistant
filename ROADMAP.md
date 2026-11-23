# Roadmap

## Hubble 3667 controls

The integration now reads temperature, Wi-Fi strength, bitrate and brightness,
and supports local changes to night vision, bitrate, brightness and ceiling
mount through the authenticated Orbweb mapping. Home Assistant requests an
updated reading after a successful change. The 0667 and 1667 retain their
direct local HTTP path. [Current model coverage](https://github.com/jingle-jew/hubble-connected-home-assistant/wiki/Devices-and-features)

The remaining questions require observations on owner-authorised cameras.

- Does the 3667 support switching the indicator LED? Verify the transport,
  accepted values, acknowledgement and resulting device state before exposing
  a control.
- Which settings survive a cold boot on the tested firmware, and do other
  firmware revisions accept the same values?
- How reliably does an Orbweb stream recover across models and firmware when
  its local tunnel expires?

Report the model, firmware and observed result without camera identifiers or
credentials. [Contribution guide](https://github.com/jingle-jew/hubble-connected-home-assistant/wiki/Development-and-contributing)
