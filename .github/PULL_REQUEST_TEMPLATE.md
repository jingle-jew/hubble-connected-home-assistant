## Summary

Describe the smallest behavior change and why it is needed.

## Evidence and validation

- Observation or compatibility basis:
- Automated tests:
- Manual validation, if any:

## Clean-room checklist

- [ ] I included no proprietary binaries, assets, decompiled source, or packet captures.
- [ ] Test data is synthetic and contains no real account, device, or network identifiers.
- [ ] The change does not bypass authentication or target third-party devices.
- [ ] New binary parsing validates sizes, ranges, roles, and identity relationships.
- [ ] State-changing behavior is explicit, bounded, documented, and tested.
- [ ] `ruff check custom_components tests tools` passes.
- [ ] `python -m unittest discover -s tests -v` passes.
