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
- Established that the format is a **hybrid** of fixed-layout blocks and TLV.
- **Header model:** the third header token is the application build, confirmed
  by the global-header version value across 98 public files.
- **Numeric column data:** FPC-compressed blocks decode across the public corpus
  (spec §2.6). Not yet compared with the reference application.
- **Asset directory**, 16-byte form: locates embedded PNGs (spec §2.4).

## Phases

1. **Reference comparison.** Generate known-content files and compare decoded
   values, turning the column-data result into a verified compatibility claim.
2. **Remaining column forms.** Run values, `04` payloads, text columns, FPC
   blocks with further tokens, trailers; then matrices (2-D). Completes the
   primary C2 data-recovery milestone.
3. **Analysis provenance.** Parse the stored-analysis XML (spec §2.8): operations
   and input ranges, which no open reader recovers today.
4. **Object tree.** Recover nesting and boundaries (folders → books → sheets →
   columns; windows/graphs).
5. **Per-object field schemas.** For each object type, map the positional field
   schema (name, long name, comment, units, dimensions, flags, …), with a record
   catalog as needed.
6. **Embedded assets.** Pin the 36-byte directory form; optionally extract images.
7. **Legacy `.opj` delta.** Reconcile with existing open `.opj` readers; decide
   what `opj` adds beyond them.

## How to help

Small, independently verifiable units — a verified record-type finding, a
failing fixture, a decoded field with the bytes that show it. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the organization
[research program](https://github.com/exbound/research).
