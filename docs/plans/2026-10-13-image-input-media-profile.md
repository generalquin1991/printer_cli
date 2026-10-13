# Image Input and Media Profiles

For Claude: REQUIRED SUB-SKILL superpowers:executing-plans

## Goal

Add generic image printing and move label geometry into a reusable media profile without changing the current TE344 defaults.

## Steps

1. Add the default TE344 media JSON and load/validate custom profile files.
2. Thread profile geometry through text, QR, bitmap, feed, back, and capabilities.
3. Add image CLI input from a local file or Base64 stdin, convert it to a centered monochrome TSPL bitmap, and declare Pillow.
4. Add focused unit tests and update Chinese/English command documentation.
5. Run the test suite and inspect CLI help/capabilities without connecting to the printer.

## Validation

Unit tests cover defaults, custom geometry, malformed profiles, image input/bitmap payloads, and CLI options. Hardware printing remains a manual verification step.
