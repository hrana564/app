# Canonical license texts

One file per SPDX license id, used by `backend/scripts/write_third_party_notice.py`
to build `THIRD_PARTY_NOTICES.txt`.

Most Python wheels and many npm packages ship no license file at all, so a
notice built only from what is installed would understate the obligation. These
files supply the full text for each license id that appears in the dependency
tree; the generator reproduces one text per distinct license and lists every
package that uses it.

The set covers every id in `ALLOWED` and `REVIEW` in
`backend/scripts/license_policy.py`, so an acceptable new dependency can be
attributed without first fetching its text. A BLOCKED id is never attributed —
the gate and the notice both refuse on it.

## Source

Downloaded verbatim from the SPDX license-list-data repository:

    https://github.com/spdx/license-list-data/tree/main/text

The `MIT.txt` text carries the standard `<year> <copyright holders>`
placeholder, which is the canonical permission notice; the per-package
copyright holders are listed in the notice's "COPYRIGHT NOTICES" section.

## Adding a license

If the generator refuses to run because it found a license with no text here,
download `licenses/<SPDX-ID>.txt` from the repository above and commit it. Do
not paste a text by hand — the point of these files is that they are verbatim.
