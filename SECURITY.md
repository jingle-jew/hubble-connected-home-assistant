# Security policy

## Supported versions

Until the first stable release, only the newest published beta is supported.
Security fixes will not be backported to older development snapshots.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities, credential exposure, or a way
to access a device without its owner's authorization. Use GitHub's private
security-advisory form:

https://github.com/jingle-jew/hubble-connected-home-assistant/security/advisories/new

Include the integration version, Home Assistant version, affected model, and a
minimal description. Do not include passwords, authentication tokens, Orbweb
identifiers, MAC addresses, public or private IP addresses, raw packet captures,
Home Assistant `.storage` files, or full diagnostics until a secure exchange is
agreed upon.

## Security model

The integration is intended only for cameras and accounts controlled by the
Home Assistant owner. It requires normal account and per-camera authentication;
it is not designed to bypass access controls.

Cloud credentials are stored by Home Assistant in config-entry storage so the
integration can reconnect after restart. Home Assistant configuration backups
must therefore be protected as secrets. The integration does not transmit
analytics or credentials to the project maintainer.

Loopback RTSP endpoints are bound to `127.0.0.1`. The legacy Android bridge is
not part of the supported public runtime.
