# Roadmap

`opj` reconstructs the Origin project-file format one independently verifiable
layer at a time. The format is deep — a document is a tree of objects
(folders, workbooks/worksheets, matrices, graphs, notes) whose records mix
fixed-layout binary structs with length-prefixed TLV (see
[`docs/engineering-spec.md`](docs/engineering-spec.md)). Recovering actual cell
data is a campaign, not a single step; this file is the plan.

Everything is built clean-room from the functional spec and the observable bytes
of publicly published sample files, and each phase is validated against those
samples with the coverage triplet (reference observed / implemented / verified).

## Done

- Container + header line (`CPYUA <format-version> <build>`) and the per-document
  root header.
- The `0x80–0x9F` TLV record form (strings + short scalars; compact-float).
- **Object-name inventory**: a resync walk recovers worksheet/column and
  analysis-object names, with the new-object vs. continuation distinction —
  verified against two samples.
- **Stream model:** the record stream is a run-length layer over fixed-layout
  structures (spec §2.1). Column records expand to a fixed size with the name at
  the offset legacy `.opj` headers use.
- **Header model:** the third header token is the application build, confirmed
  by the global-header version value across 98 public files.
- **Column data:** every column payload in the public corpus parses, holding
  doubles, strings, or integer offsets into LZ4 string sections (spec §2.6, §2.7).
  Not yet compared with the reference application.
- **Asset directory**, 16-byte form: locates embedded PNGs (spec §2.4).

## Phases

1. **Reference comparison.** Generate known-content files and compare decoded
   values, turning the column-data result into a verified compatibility claim.
   This also settles the special value codes and flag meanings.
2. **Whole-stream walk.** Expand the run-length layer end to end and catalog the
   record types.
3. **Legacy field mapping.** Compare expanded column records with the open `.opj`
   header knowledge (type, width, long name, units, …).
4. **Object tree.** Recover nesting and boundaries (folders → books → sheets →
   columns; windows/graphs).
5. **Matrices.** Determine whether 2-D data shares the column payload form.
6. **Analysis provenance.** Find where a project's actual operations and fit
   results live. The settings document (spec §2.8) does not appear to be it.
7. **Embedded assets.** Pin the 36-byte directory form; optionally extract images.
8. **Legacy `.opj` delta.** Reconcile with existing open `.opj` readers; decide
   what `opj` adds beyond them.

## How to help

Small, independently verifiable units — a verified record-type finding, a
failing fixture, a decoded field with the bytes that show it. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the organization
[research program](https://github.com/exbound/research).
