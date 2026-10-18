# opj — Origin project-file reader

An independent, open, compatible reader for Origin project files (`.opju`, and
the legacy `.opj`). The goal is to recover the data and document structure from
Origin projects without the vendor's application — reading the container, its
object inventory, and its metadata, as the first step toward a compatible
document model.

This is an Exbound implementation repository. Exbound builds independent,
compatible reimplementations of proprietary scientific and engineering software;
the vendor application is the **behavioral reference, not the architectural
blueprint**. See the [research program](https://github.com/exbound/research) for
the C1–C9 capability model and the Origin dossier.

## Status

Early. The reader parses the container header and the document-header fields,
and recovers the internal **object-name inventory** (worksheet/column and
analysis-object names, with the new-object vs. continuation distinction) from
real published files. Numeric cell data and embedded assets live in binary
sub-blobs that are not yet decoded. See
[`docs/engineering-spec.md`](docs/engineering-spec.md) for the specification and
the open questions, each marked established / hypothesis / needs-observation.

| Capability | Reference observed | Implemented | Verified |
|------------|--------------------|-------------|----------|
| C2 header fields | yes (public samples) | yes | yes (2 samples) |
| C2 object-name inventory (resync walk) | yes | yes | yes (2 samples) |
| C2 numeric cell data / binary sub-blobs | partial | no | no |
| C2 embedded-asset delimiting | partial | no | no |
| C3 document model | no | no | no |

The resync walk reliably recovers distinctive text (names, comments); records it
reports *inside* a binary data/asset sub-blob are candidates, not decoded
fields — only the name inventory is validated.

Recovering actual cell data is a multi-phase campaign (the format is a hybrid of
fixed-layout binary records and TLV); see [`ROADMAP.md`](ROADMAP.md).

## Install and use

Requires Python 3.11+. From a checkout:

```sh
just setup          # create the venv and install
python -m opj path/to/project.opju
```

The CLI prints the header fields, the root header, and the recovered object-name
inventory, and flags the opaque (embedded-asset) regions it does not yet decode.

## Build and test

```sh
just check   # format-check + lint + typecheck
just test    # run the test suite
```

Tests run against publicly published Origin samples referenced by provenance
(not committed); see [`tests/fixtures/README.md`](tests/fixtures/README.md). With
no fixtures present, the sample-dependent tests skip.

## Compatibility and boundaries

Reads Origin project files; it is **not** affiliated with or endorsed by the
vendor, and is described nominatively ("reads Origin projects"), never named
after the product. This software:

- operates on **lawfully held** files only;
- is built **clean-room** from a functional specification
  ([`docs/engineering-spec.md`](docs/engineering-spec.md)) and the observable
  bytes of publicly published sample files — it contains no vendor code, symbol
  names, binary internals, artwork, or trademarks;
- ships **no** circumvention of any protection measure.

File-format facts (magic numbers, field offsets, record framing) are functional
facts, not protected expression. See the research program's
[legal basis](https://github.com/exbound/research/blob/main/docs/legal-basis.md).
This is orientation, not legal advice.

## License

[Apache-2.0](LICENSE). Chosen as a permissive license with an explicit patent
grant (the legal basis notes that patents can bind an independent
implementation); this reader is clean-room-from-scratch and links no copyleft
engine.
