# Engineering specification — Origin project files (`.opj` / `.opju`)

The enumerated, functional description of the Origin project-file format that
this reader implements, and how compatibility is measured. This is a functional
specification: it states the on-disk format as observable facts, derived by
analysis and verified against publicly published sample files. The
implementation is built from this document and the observable sample bytes
alone.

- **Formats:** `.opju` (the Unicode project format, Origin 2018 generation and
  later) and the legacy `.opj`. **This repo targets `.opju` first.**
- **Registry:** PRONOM `fmt/1124` (OPJU).
- **Capabilities in scope:** C2 (data compatibility) — read the container,
  recover the object inventory and document metadata. C3+ later.

Each statement is marked **[E]** established (observed and verified against real
files), **[H]** hypothesis, or **[N]** needs-observation.

## 1. File layout

### 1.1 Header line **[E]**

The file begins with a single ASCII header line, terminated by `\n` (`0x0A`):

```
CPYUA <format-version> <build>
```

e.g. `CPYUA 4.3330 172`. `<format-version>` is a decimal (observed 4.3330–4.3774
across the corpus in §3). `<build>` is the application **build number**, not an
object count: it equals the build digits of the version value in the global
header (§1.3) in 98 of 98 files, e.g. `172` with `9.600172`. The leading token
belongs to a `CPY…` magic family; `CPYUA` is the Unicode `.opju` variant. Legacy
`.opj` uses a different member of the family. **[N]** exact legacy magic.

### 1.2 Body and root header **[E]**

The body begins immediately after the header newline. Its first **7 bytes** are
a per-document root header:

```
XX 01 6c c0 11 01 NN
```

Bytes `[2:6]` = `6c c0 11 01` are **constant** across documents. `XX` (byte 0)
and `NN` (byte 6) vary per document. **[N]** the role of `XX`/`NN`. The record
stream begins at **body+7**.

### 1.3 Preview field and global header **[E]**

The opening records end with the 8-byte ASCII marker `PrvwOPJU` followed by a
little-endian `u64`, the asset-directory file offset (§2.4). Immediately after
it, in 98 of 98 files:

```
[u32 size] 0A [size bytes] 0A      global header
00 00 00 00 0A                     zero-size terminator
```

The global header is a fixed-layout block. The little-endian `f64` at its offset
`0x1B` is the application version plus the build scaled by 10⁻⁶: `9.600172` is
version 9.6, build 172; `9.800193` is version 9.8, build 193. The version has two
decimal places and the build follows them, which splits all 98 files with the
build agreeing with the header line. Observed versions run 9.6–10.3. **[N]** the
other global-header fields.

## 2. Record stream

From body+7 the body is a **tag / length / value byte stream**, consumed by a
forward byte cursor. The reader repeatedly reads one tag byte and dispatches on
its value.

### 2.1 Length-prefixed value records, tags `0x80`–`0x9F` **[E]**

```
[tag: 1 byte, 0x80–0x9F] [length: 1 byte L] [payload: L bytes]
```

The payload is either text (UTF-8/ASCII) for name and comment fields, or a
short little-endian numeric for scalar fields. **[H]** the low 5 bits of the tag
encode a field/role id; the high bits mark the value class. This model has been
verified to recover, from the real samples, document comment strings and the
internal object-name inventory (worksheet names such as `Book1_A` … `Book1_I`,
`Book2_A`; analysis-object names such as `PrRhSn3_A` … `PrRhSn3_E`).

### 2.2 String framing **[E]**

A text record is `[tag][L][L bytes]` with `L` the exact byte count. Verified: a
length-prefixed scan on this rule recovers the named objects above cleanly.
Runs of 4–5 printable bytes without a valid preceding length byte are
coincidental data, not strings.

### 2.3 Numeric framing **[E]** / **[H]**

Short scalars use the same `[tag][L][payload]` framing. A 2-byte payload
`f0 3f` decodes to `1.0` as the **high 16 bits** of a little-endian IEEE-754
double (the low 6 bytes implied zero) — a compact float encoding, **[E]**
verified for that value. Whether a given 2-byte field is a compact float or a
`u16` integer is position-dependent **[H]**; a tag names a value's type/width
class, not its role (see §2.5). **[N]** the full scalar encoding table.

### 2.4 Embedded binary assets **[E]**

The document embeds binary blobs within the stream:

- **Image data**: real files contain PNG, BMP, and EMF payloads (verified by
  signature — e.g. a sample contains 21 PNG blocks).

These assets are **not** part of the `0x80–0x9F` length-prefixed TLV. The
high-byte regions (`0xC0`–`0xFF`) that a naive 1-byte-length parse chokes on
include these payloads.

**Asset directory [E] / [N].** The `u64` after `PrvwOPJU` (§1.3) is the absolute
file offset of a directory:

```
u64 count | u32 0 | u32 table-bytes | count entries of table-bytes/count bytes
```

- **16-byte entries [E]** (`u64 offset`, `u64 length`, both absolute): 51 files.
  Every entry delimits a complete PNG, signature through `IEND`.
- **36-byte entries [N]**: 42 files. Each begins with a `u64`, but only some of
  those are offsets of a PNG; the remaining fields are unknown.
- 5 files match neither shape **[N]**.

A directory need not list every image: across those 51 files, 131 of 135 PNG
signatures sit at an entry start.

### 2.5 Object and column records **[E]**

A document object is an ordered run of `[tag][len][val]` field records. A
worksheet column, for example, appears as a fixed field group immediately before
its name record. Observed consistently in a real sample (identical across a
book's columns):

```
83 02 <2 bytes> | 85 02 f0 3f | 83 02 f0 3f | 85 01 0a | 91 01 01 | 82 07 <name>
```

where `0x85`/`0x83` carry scalar fields (the 2-byte `f0 3f` is the compact-float
`1.0`), `0x91` carries a 1-byte flag, and `0x82` carries the column name. The
**first** column of a book carries its name under a higher, object-type-dependent
tag (`0x97` for a workbook, `0x95` for an analysis object); continuation columns
use `0x82`. **[E]** this new-object-vs-continuation distinction, verified across
both samples.

**A tag names a value's type/width class, not its field role.** The same tag
carries different lengths and meanings at different positions; a field's role
comes from its position in the object type's field sequence, not from the tag.
So the goal is **per-object field schemas**, not a global tag→role map. **[N]**
the field schema for each object type, and the object-boundary markers.

### 2.6 Column data payloads **[E]** / **[N]**

A column's data is not TLV. It follows the column's name record as a descriptor
and a payload:

```
[tag 0x80–0x9F] 18 | u64 24 | u64 L | 8 bytes | 0A | L bytes of payload
```

**[N]** the 8 descriptor bytes. Searching for the descriptor's first 10 bytes
also finds coincidental matches, so `L` must be validated: of 1039 candidates in
the corpus, 903 have payloads ending in the two bytes `CE FA`.

**Payload.** 855 of those begin with `05`, in this form:

```
05 <LEB128 a> FF FF <LEB128 b> <1 byte> <token stream> CE FA
```

`b` counts the values the stream holds. `a` is the row count including trailing
empty rows (`a == b` in 379 of 816, `a > b` in 437, and never less; see trailers
below). **[N]** the single byte (`00` in 786 of 816, and in all 705 decoded FPC
blocks), the `04` payload form (48), and `05` payloads lacking `FF FF` (39).

**Token stream.** Each token starts with a LEB128 varint `v`:

- **Odd `v` — FPC block [E]** (below). `(v >> 1) + 1 == b` in 705 of 795 such
  streams. **[N]** the other 90, where the block appears to hold fewer than `b`
  values and further tokens follow.
- **Even `v` — run [E] / [N]**: `v >> 1` copies of one following fixed-width
  value. Streams made only of runs sum to exactly `b` in 14 of 21 even-first
  streams, 12 of those being a single run of 1-byte values, which is weak
  evidence. **[N]** where the value's width and type are stated, and the other 7.

**FPC block [E].** The values are little-endian doubles compressed with FPC:
M. Burtscher and P. Ratanaworabhan, "FPC: A High-Speed Compressor for
Double-Precision Floating-Point Data," *IEEE Trans. Computers* 58(1), 2009. The
predictors and hashes are as published. Laid out here as:

- The byte after `v` is the table size in bits (`0C`, 12 bits, in all 705): both
  tables hold 2^12 entries, zero-initialized.
- Codes and residuals are **interleaved**: one byte holds two 4-bit codes, low
  nibble first, and is followed directly by those two values' residuals. This
  differs from the block layout in the paper.
- In a code, the high bit selects the predictor (1 = DFCM, 0 = FCM). The low 3
  bits index the residual length `[0, 1, 2, 3, 5, 6, 7, 8]` bytes; there is no
  length 4. A residual is little-endian with its high zero bytes omitted.
- value = prediction XOR residual, on the 64-bit pattern.
- For an odd count, the last code byte's high nibble is unused and has no
  residual, unlike the paper's spurious value: 118 of the 294 exactly consumed
  blocks have odd counts.

Evidence over the 705 blocks and 265177 values, as the share of decoded values
with a short decimal form (at most 9 significant digits):

| Parameters | Share |
|---|---|
| 12-bit tables, as above | 0.757 |
| any other table size tried (8, 10, 16, 20, 24 bits) | ≤ 0.141 |
| DFCM hash right-shift 48 instead of 40 | 0.127 |
| FCM hash left-shift 5 instead of 6 | 0.108 |
| opposite predictor bit | 0.023 |

All 705 blocks decode without overrun: 294 consume the stream exactly and 411
leave trailing bytes. Absence of overrun does not test these parameters, because
residual lengths come from the codes alone; every variant above also has none.

**Trailers [E] / [N].** After a block, `FF FF <LEB128 n> 01 <LEB128 2n> 00` states
`n` trailing empty rows, with `n + b == a`: 391 of the 411 trailers (384 of 6
bytes, 7 of 8 bytes). **[N]** the other 20.

*Values are not yet verified against the reference application.* A public-fact
check: a periodic-table sample's first column equals the IUPAC group of elements
Z = 1–54, 54 of 54.

**Do not derive an implementation from the authors' FPC source code.** It is
under a non-commercial license. Implement from this document and the paper.

**Reader caveat.** A forward walk that resyncs across these regions (§ the
reference reader) reliably recovers distinctive **text** (names, comments), but
*inside* a payload it may report coincidental `[tag][len][payload]` "records"
that are not real fields. Records that fall inside data/asset regions are
candidates, not decoded fields.

### 2.7 Record model: fixed blocks + TLV **[E]** / **[N]**

The on-disk records are **not uniform**:

- **Fixed-layout blocks** — the sized global header (§1.3).
- **Length-prefixed TLV records** — the `0x80–0x9F` `[tag][len][val]` form of
  §2.1–2.5, used for object names, comments, and short values.

**[H]** Other records also carry a fixed-layout part. **[N]** a record-type
catalog, and the full object nesting that places each column in its sheet.

### 2.8 Long text **[E]**

Text longer than one length byte can state is cut into chunks: a tag, then
repeated `7F` + 127 bytes. Removing the `7F` bytes repairs words split across
chunks. XML with an `<OriginStorage>` root is stored this way, and holds stored
analysis operations and their input ranges. **[N]** how a chain ends, and the
schema of this XML.

## 3. Compatibility oracle

- **Fixtures:** publicly published OriginLab `.opju` files (see
  `tests/fixtures/README.md`), referenced by provenance, not committed.
- **Corpus:** figures in this document are measured over 98 public files: the 96
  `.opju` in [`originlab/Templates`](https://github.com/originlab/Templates) at
  `fa2522a`, plus `SampleData.opju` (`originlab/Apps`) and `Python - Fit
  Summation.opju` (`originlab/Noho-Training`). `tests/test_corpus.py` asserts the
  counts when `OPJ_CORPUS` names a directory holding the files. It does not assert
  the short-decimal shares in §2.6, which compare deliberately wrong parameters
  the reader does not expose.
- **Pass criteria (C2, initial):** the reader recovers, for each fixture, the
  header fields (`format-version`, `build`), the root header, and the
  object-name inventory, matching an independently recorded expected list.
- **Reference status:** no value has been compared with the reference
  application. Generating known-content files for that comparison is outstanding.

## 4. Open questions (prioritized)

See [`ROADMAP.md`](../ROADMAP.md) for the phased plan.

1. **[N]** Column value types: run values, `04` payloads, and `05` payloads
   lacking `FF FF` (§2.6), plus FPC blocks followed by further tokens.
2. **[N]** The 20 trailers that do not state empty rows (§2.6).
3. **[N]** Matrix storage: whether matrices share the column payload form.
4. **[N]** Stored-analysis XML: chain end and schema (§2.8).
5. **[N]** Record-type catalog (§2.7).
6. **[N]** Per-object field schemas: which position carries name, long name,
   comment, units, dimensions, etc. (roles are positional, not per-tag — §2.5).
7. **[N]** Object nesting and boundaries — the document tree.
8. **[N]** 36-byte asset-directory entries (§2.4).
9. **[N]** Scalar numeric encodings (§2.3).
10. **[N]** Legacy `.opj` delta vs. what existing open `.opj` readers cover.
