# Contributing

Contributions are welcome when they preserve the project's clean-room and
owner-authorization boundaries.

## Clean-room requirements

Do not submit:

- vendor APKs, JARs, firmware, libraries, icons, or other proprietary assets;
- copied or translated decompiled source;
- packet captures or packet payloads;
- real account credentials, tokens, camera identifiers, MAC addresses, email
  addresses, hostnames, or network addresses;
- features intended to bypass authentication or operate third-party devices.

Describe protocol evidence in terms of independently observed behavior. Test
fixtures must use `example.com`, RFC 5737 documentation networks, locally
administered MAC addresses, and clearly synthetic identifiers.

## Development workflow

Keep changes narrow and preserve the boundaries between cloud inventory, local
camera commands, Orbweb transport, and Home Assistant entities. New protocol
parsers must validate lengths, ranges, roles, and identity relationships before
accepting data.

For state-changing controls, document the exact verified range and add tests.
Do not add generic raw-command services.

Run before opening a pull request:

```sh
ruff check custom_components tests tools
python -m unittest discover -s tests -v
```

Explain which behavior was observed, what remains uncertain, and how the test
data was synthesized. Confirm in the pull request that no proprietary material
or real device/account data is included.
