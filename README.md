# opj — Origin project-file reader

An independent, open, compatible reader for Origin project files (`.opju`, and
the legacy `.opj`). The goal is to recover the data and document structure from
Origin projects without the vendor's application — reading the container, its
object inventory, its metadata, and its FPC-compressed column values, as the
first step toward a compatible document model.

This is an Exbound implementation repository. Exbound builds independent,
compatible reimplementations of proprietary scientific and engineering software;
the vendor application is the **behavioral reference, not the architectural
blueprint**. See the [research program](https://github.com/exbound/research) for
the C1–C9 capability model and the Origin dossier.

## Status

Early. The reader parses the container header, the document-header fields, and
the global header's version and build; recovers the internal **object-name
inventory** (worksheet/column and analysis-object names, with the new-object vs.
continuation distinction); delimits assets through the 16-byte asset directory;
and decodes column payloads that hold one FPC block of `b` values. Other column
and directory forms are reported as undecoded, with the reason, rather than
guessed. See [`docs/engineering-spec.md`](docs/engineering-spec.md) for the
specification and the open questions, each marked established / hypothesis /
needs-observation.

**Nothing here has been verified against the reference application.** "Observed"
means seen in the bytes of the 98 public files in the specification's corpus;
those files are the reference's output, but no value has been compared with what
the application itself reports, so the Verified column is `no` throughout. What
backs the code is the specification's corpus figures and public-fact sample
checks, as noted.

| Capability | Reference observed | Implemented | Verified against reference | Backed by |
|------------|--------------------|-------------|----------------------------|-----------|
| C2 header fields, global-header version and build | yes (98 files) | yes | no | corpus figures |
| C2 object-name inventory (resync walk) | yes (2 samples) | yes | no | sample inventory tests |
| C2 asset directory, 16-byte entries | yes (51 files) | yes | no | corpus figures |
| C2 asset directory, other shapes | yes (47 files) | no: reported unsupported | no | |
| C2 column values, single FPC block | yes (705 payloads) | yes | no | corpus figures, public-fact checks |
| C2 column values, other forms | partial (198 payloads) | no: reported undecoded | no | |
| C2 column names | partial | best-effort | no | sample tests |
| C2 embedded-asset contents | partial (signatures) | no | no | |
| C2 matrices | no | no | no | |
| C3 document model | no | no | no | |

The resync walk reliably recovers distinctive text (names, comments); records it
reports *inside* a binary data/asset sub-blob are candidates, not decoded
fields.

Recovering all cell data is a multi-phase campaign (the format is a hybrid of
fixed-layout binary records and TLV); see [`ROADMAP.md`](ROADMAP.md).

## Install and use

Requires Python 3.11+. From a checkout:

```sh
just setup          # create the venv and install
python -m opj path/to/project.opju
```

The CLI prints the header fields, the build and version, an asset-directory
summary, the root header, the recovered object-name inventory, and each column's
name, row count and first few values. It flags the undecoded column forms and
the opaque regions it does not yet decode.

## Build and test

```sh
just check   # format-check + lint + typecheck
just test    # run the test suite
```

Tests run against publicly published Origin samples referenced by provenance
(not committed); see [`tests/fixtures/README.md`](tests/fixtures/README.md). With
no fixtures present, the sample-dependent tests skip. `tests/test_corpus.py`
reproduces the specification's corpus figures and skips unless `OPJ_CORPUS` is
set (see [`docs/setup.md`](docs/setup.md)).

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
