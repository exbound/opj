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

- Container + header line (`CPYUA <version> <object-count>`) and the per-document
  root header.
- The `0x80–0x9F` TLV record form (strings + short scalars; compact-float).
- **Object-name inventory**: a resync walk recovers worksheet/column and
  analysis-object names, with the new-object vs. continuation distinction —
  verified against two samples.
- Established that the format is a **hybrid** of fixed-layout binary records and
  TLV, and that bulk cell data lives in a per-object mechanism (not a plain
  array).

## Phases

1. **Record model.** Catalog the record types and pin the boundary between a
   record's fixed-struct part and its TLV tail. Make the reader split a record
   into (fixed fields, TLV tail, data sub-blob) deterministically.
2. **Object tree.** Recover nesting and boundaries (folders → books → sheets →
   columns; windows/graphs) and reconcile the recovered object count with the
   header's `<object-count>`.
3. **Per-object field schemas.** For each object type, map the positional field
   schema (name, long name, comment, units, dimensions, flags, …).
4. **Cell data.** Decode bulk numeric data: worksheet columns (1-D) first, then
   matrices (2-D). This is the primary C2 data-recovery milestone.
5. **Embedded assets.** Pin the length encoding for the preview bitmap and
   embedded images; optionally extract them.
6. **Legacy `.opj` delta.** Reconcile with existing open `.opj` readers; decide
   what `opj` adds beyond them.

## How to help

Small, independently verifiable units — a verified record-type finding, a
failing fixture, a decoded field with the bytes that show it. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the organization
[research program](https://github.com/exbound/research).
