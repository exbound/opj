# Test fixtures

Fixtures are the evidence a compatibility claim is tested against: native files,
recorded reference computations, and documented operations with expected
results.

## What may live here

- **Self-authored** files created with the reference product under a lawful
  license, where their content is your own data (not vendor sample content).
- **Openly licensed** sample files, with the license recorded.
- **References by provenance** — a pointer (URL + checksum) to a lawfully
  published file, fetched at test time rather than committed, when the file
  itself cannot be redistributed here.

## What must not live here

- **Vendor binaries** or any vendor-distributed program files.
- **Vendor-copyrighted sample content** (the product's bundled example projects,
  artwork, help content).
- Anything obtained by defeating a DRM, dongle, or activation check.

Each fixture records where it came from and why it is lawfully usable. A fixture
whose provenance cannot be stated does not belong in the suite.

## Fixtures the suite looks for

Publicly published by OriginLab on GitHub, not committed here. Save each under
the local name, in a directory named by `OPJ_FIXTURES` or `tests/fixtures/_local/`.

| Local name | Source | SHA-256 |
|---|---|---|
| `SampleData.opju` | `originlab/Apps`: `Samples/SampleData.opju` | `9b213f2f053f04678fe417976f00b91c11acb6413c5f9e5db3a037a144a3d3a8` |
| `FitSummation.opju` | `originlab/Noho-Training`: `OPJUs/Python - Fit Summation.opju` | `4bc357a24cda929b2239a10228145c141df74d03745b43bffe2b0da6993f4093` |
| `PeriodicTable.opju` | `originlab/Templates` at `fa2522a`: `Graph Templates/Specialized/Periodic Table/PeriodicTable.opju` | `1273c9fdda12d0789f4bde1a0de4e494e1e29c4d2fa3776ba5be5ce5f8689926` |
| `XPSArea.opju` | `originlab/Templates` at `fa2522a`: `Graph Templates/Specialized/XPS Area/XPSArea.opju` | `5728ebcfff5ecfda2de0d6a3e399d44dd2fd599b410fda8e9cb4e0aa302a8101` |

The corpus test (`OPJ_CORPUS`) reads a flat directory of these files plus every
`.opju` in `originlab/Templates` at `fa2522a`, 98 in all.
