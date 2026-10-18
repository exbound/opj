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

### 2.3 Numeric framing **[H]**

Short scalars use the same `[tag][L][payload]` framing. A recurring 2-byte
payload is consistent with the high word of an IEEE-754 double (e.g. the bytes
for `1.0`), suggesting a compact/truncated float encoding. **[N]** confirm the
scalar encodings (widths, int vs float, endianness).

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

where `0x85`/`0x83` carry scalar fields (the 2-byte `f0 3f` is the high word of
IEEE-754 `1.0`, a compact-float encoding), `0x91` carries a 1-byte flag, and
`0x82` carries the column name. The **first** column of a book carries its name
under tag `0x97` rather than `0x82`. **[H]** the high nibble / specific tag id
distinguishes a new object (`0x97`) from a continuation field (`0x82`), and the
low bits select the field role (name / long-name / scalar / flag). **[N]** the
full tag-id → role map, and the object-start/boundary markers needed to count
objects against the header's `<object-count>`.

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
