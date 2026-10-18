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
CPYUA <format-version> <object-count>
```

e.g. `CPYUA 4.3330 172`. `<format-version>` is a decimal (observed 4.33xx–4.34xx
in the 2018+ generation); `<object-count>` is a decimal integer. The leading
token belongs to a `CPY…` magic family; `CPYUA` is the Unicode `.opju` variant.
Legacy `.opj` uses a different member of the family. **[N]** exact legacy magic
and the meaning of `<object-count>` (whether it counts top-level objects or all
records).

### 1.2 Body and root header **[E]**

The body begins immediately after the header newline. Its first **7 bytes** are
a per-document root header:

```
XX 01 6c c0 11 01 NN
```

Bytes `[2:6]` = `6c c0 11 01` are **constant** across documents. `XX` (byte 0)
and `NN` (byte 6) vary per document. **[N]** the role of `XX`/`NN`. The record
stream begins at **body+7**.

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

- A **preview thumbnail**, introduced by the ASCII section marker `PrvwOPJU`
  ("Preview OPJU"). The preview is a serialized **bitmap** with its own size
  field. **[N]** its exact length encoding (the key blocker for a linear walk
  past it).
- **Image data**: real files contain PNG, BMP, and EMF payloads (verified by
  signature — e.g. a sample contains 21 PNG blocks).

These assets are **not** part of the `0x80–0x9F` length-prefixed TLV; the
high-byte regions (`0xC0`–`0xFF`) that a naive 1-byte-length parse chokes on are
these embedded payloads. **[N]** the exact length/offset encoding that delimits
each asset — the reader discovers and records this against the samples.

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
the field schema for each object type, and the object-boundary markers needed to
count objects against the header's `<object-count>`.

### 2.6 Binary data sub-blobs **[E]** / **[N]**

A worksheet column's **numeric data** is stored in a binary sub-blob after the
name (a descriptor carrying small integers — e.g. a count — then raw values),
**not** as `0x80–0x9F` TLV. This is the same shape as the embedded preview
(§2.4): a self-contained binary region inside the record stream. **[N]** the
sub-blob format (row count, data type code, value encoding) — the main open item
for recovering actual cell data.

**Reader caveat.** A forward walk that resyncs across these regions (§ the
reference reader) reliably recovers distinctive **text** (names, comments), but
*inside* a binary sub-blob it may report coincidental `[tag][len][payload]`
"records" that are not real fields. Only the recovered text inventory is
validated; records that fall inside data/asset sub-blobs are candidates, not
decoded fields.

## 3. Compatibility oracle

- **Fixtures:** publicly published OriginLab `.opju` files (see
  `tests/fixtures/README.md`), referenced by provenance, not committed.
- **Pass criteria (C2, initial):** the reader recovers, for each fixture, the
  header fields (`format-version`, `object-count`), the root header, and the
  object-name inventory, matching an independently recorded expected list.

## 4. Open questions (prioritized)

1. **[N]** Tag-id → field-role map (which tag ids are object name, long name,
   comment, units, dimensions, …).
2. **[N]** Object nesting and boundaries — where one document object ends and
   the next begins.
3. **[N]** Embedded-asset length/offset encoding.
4. **[N]** Scalar numeric encodings (§2.3).
5. **[N]** Legacy `.opj` delta (how much the existing open `.opj` readers already
   cover vs. `.opju`).
