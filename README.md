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

Early. The reader parses the container header and the global header's version
and build; expands the run-length layer; reads column records for their names and
row fields; joins columns to curve records for their type and labels; decodes
column payloads to all their rows (doubles, strings, and integers resolved
through LZ4-compressed string sections); delimits assets through the 16-byte asset
directory; and exposes the stored settings document as raw bytes. Compact-double
codes whose meaning is not established stay as distinct markers, and an
integer that cannot be resolved stays an integer, with the failure recorded. The
stream is not walked end to end. See
[`docs/engineering-spec.md`](docs/engineering-spec.md) for the specification and
the open questions, each marked established / hypothesis / needs-observation.

**Nothing here has been verified against the reference application.** "Observed"
means seen in the bytes of the 98 public files in the specification's corpus;
those files are the reference's output, but no value has been compared with what
the application itself reports, so the Verified column is `no` throughout. What
backs the code is the specification's corpus figures and public-fact sample
checks, as noted.

| Capability | Reference observed | Implemented | Verified against reference | Backed by |
|------------|--------------------|-------------|----------------------------|-----------|
| C2 header fields, global-header version and build | yes (98 files) | yes | no | corpus figures |
| C2 text inventory (literal-run scan) | yes (2 samples) | yes | no | sample inventory tests |
| C2 run-length layer | partial (column records, settings document) | yes | no | corpus figures |
| C2 column names (expanded record) | yes (903 records) | yes | no | corpus figures |
| C2 column values: doubles, strings, integers | yes (903 payloads) | yes | no | corpus figures, public-fact checks |
| C2 column record fields: total, first and last row, value size | yes (903 records; first and last row differ from the data in 1 and 25 of 891) | yes, raw | no | corpus figures |
| C2 column type and labels, joined through curve records | yes (807 of 903 columns reach a record by the rule) | yes, with a hypothesized sheet rule; the other 96 are left empty with the reason | no | corpus figures, public-fact check |
| C2 type value 1, label markup, stray control bytes in labels | yes | no: returned raw | no | |
| C2 compact-double codes `64`, `65` | yes (1892 rows) | no: kept as markers | no | |
| C2 integer resolution through string sections | yes (48 sections) | yes, by a hypothesized rule | no | corpus figures, public-fact check |
| C2 settings document | yes (98 files) | raw bytes only | no | corpus figures |
| C2 asset directory, 16-byte entries | yes (51 files) | yes | no | corpus figures |
| C2 asset directory, other shapes | yes (47 files) | no: reported unsupported | no | |
| C2 embedded-asset contents | partial (signatures) | no | no | |
| C2 whole-stream walk, record catalog | no | no | no | |
| C2 matrices | no | no | no | |
| C3 document model | no | no | no | |

The text scan recovers names and comments from literal runs without expanding the
stream. Its hits are candidates: they include coincidental matches and pieces of
stored XML.

The settings document's values can hold local paths and usernames from other
machines, so the CLI prints only its size and its number of direct children.

## Install and use

Requires Python 3.11+. From a checkout:

```sh
just setup          # create the venv and install
python -m opj path/to/project.opju
```

The CLI prints the header fields, the build and version, an asset-directory
summary, the root header, the settings document's size and number of direct
children, counts of string sections and text candidates, and each column's name,
type, long name, units, row count and first few values, with any parse, resolution
or join problem.

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
