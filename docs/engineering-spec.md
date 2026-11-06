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

### 2.1 Run-length layer **[E]** / **[H]**

From body+7 the stream is **run-length coded**. A reader expands tokens into a
logical byte stream, and the fixed-layout structures are in that logical stream:

| Token byte `t` | Meaning |
|---|---|
| `0x00`–`0x7F` | `t` literal bytes follow |
| `0x80`–`0xBF` | `(t & 0x3F) + 3` zero bytes |
| `0xC0`–`0xFF` | `(t & 0x3F) + 3` copies of the next byte |

So what looks like a tag, length and value is usually a zero run followed by a
literal run. `85 02 f0 3f` is 8 zero bytes and then `f0 3f`, not a typed field.

**[E]** for column records (§2.5, 903 of 903) and the settings document (§2.8, 98
of 98). **[H]** for the rest of the stream, which is not yet walked end to end.
Column payloads (§2.6), string sections (§2.7) and listed PNGs (§2.4) sit in the
file uncoded.

Evidence for the zero-run base, over 903 column records:

| Base | Expanded record sizes |
|---|---|
| 3 | 147 bytes in 900, 130 in 3 (those 3 are mis-framed, see §2.5) |
| 2 | at least 6 sizes, largest share 136 in 362 |
| 1 | at least 6 sizes, largest share 125 in 362 |
| 0 | at least 6 sizes, largest share 114 in 362 |

These sizes come from taking the frame nearest the descriptor.

For the byte-run base, `://www.` is rebuilt in 3 of 3 URLs at base 3 and in 0 of 3
at base 2 or base 4. That is a small sample.

### 2.2 Text **[E]**

Names and comments are literal runs, so a scan for a plausible token and length
recovers them without expanding the stream: names such as `Book1_A`, and comments.
A literal run can hold more than the text, and long text continues across runs of
at most 127 bytes.

### 2.3 Numeric fields **[E]**

There is no compact float encoding in this layer. A double such as `1.0` shows as
`f0 3f` because its low six bytes are zero and fall inside a zero run. **[N]** the
fields of each structure (§2.5).

### 2.4 Embedded binary assets **[E]**

The document embeds binary blobs:

- **Image data**: real files contain PNG, BMP, and EMF payloads (verified by
  signature — e.g. a sample contains 21 PNG blocks).

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

### 2.5 Column records **[E]** / **[N]**

A column is a coded record, then a descriptor and an uncoded payload (§2.6):

```
80 <n> | <n coded bytes> | 18 | u64 24 | u64 L | 8 bytes | 0A | L bytes of payload
```

The `n` coded bytes end in a zero-run token. Expanded through §2.1, the record is
147 bytes with the column name, NUL-padded, at offset `0x58`, in 903 of 903. `80
<n>` can also occur by coincidence inside the coded bytes, so more than one frame
can end at a descriptor (6 of 903): take the nearest one that expands to 147 bytes.
`0x58` is also where the open `liborigin` reader documents the name in the legacy
`.opj` dataset header, and more of that layout carries over:

| Offset | Field | Evidence |
|---|---|---|
| `0x19` | u32 total rows | equals the decoded row count, 903 of 903 |
| `0x1D` | u32 first row | equals the first filled row, 890 of 891 columns with data |
| `0x21` | u32 last row | equals the last filled row + 1, 866 of 891 |
| `0x25`, `0x2D` | f64 | 1.0 in all 903 **[N]** meaning |
| `0x3D` | u8 stored value size | 10 in 882 of 903; larger only on text columns |

"Filled" excludes empty rows and the special codes of §2.6. **[N]** the 25 and 1
exceptions, and the other fields.

An earlier version of this document read a "new-object" tag such as `0x97` against
a "continuation" tag `0x82`. Those are zero-run lengths before the name, so they
reflect how many preceding bytes happen to be zero. **[H]** They do not mark
objects.

**[N]** the 8 descriptor bytes. Searching for the descriptor's first 10 bytes
(`[tag] 18 18 00 00 00 00 00 00 00`) also finds coincidental matches, so `L` must
be validated: of 1039 candidates, 903 have payloads ending in the two bytes
`CE FA`.

### 2.6 Column payloads **[E]** / **[H]**

All 903 payloads parse to their exact end with the row count equal to `a`:

```
payload := kind leb(a) segment* CE FA                 kind: 05 (855) or 04 (48)
segment := FF FF leb(n) flag token*                   n rows
         | u16 selector value                         1 row, flag = selector (0 or 1)
token   := leb(v)
           odd:  (v >> 1) + 1 literal values
           even: v >> 1 copies of one value
```

`leb` is LEB128. A segment of the `FF FF` form (1323) holds exactly `n` rows of
tokens; single-value segments appear 110 times with selector 0 and 48 with 1. As a
control, literal counts of `v >> 1` or `(v >> 1) + 2` each pass only 61 of 903.

The value type follows the flag and kind:

| Flag | Kind | Single or run value | Literal values | Rows |
|---|---|---|---|---|
| 1 | `05` | string: `leb(len)` + bytes; `00` is empty | same, one after another | 18414 |
| 1 | `04` | integer: `leb`, an offset into a string section (§2.7), 0 for none | same, one after another | 4602 |
| any other | either | compact double | FPC block | 357062 doubles, 1892 special |

Flag `0` is the usual double case (790 `FF FF` segments of kind `05`, 1 of kind
`04`). **[H]** Flag `3`, seen once with values near 2.4 million, marks dates. The
flag's full meaning is read from behaviour, not established.

**Compact double [E] / [H].** Code byte `c`, then `w = (c - 8) / 9` bytes, which are
the high bytes of a little-endian double whose low bytes are zero: `11 40` is 2.0,
`1a f0 3f` is 1.0, `23 20 9f 40` is 1992.0. Seen for `w` of 1, 2, 3, 4, 7 and 8.
Codes `64` (1366 rows) and `65` (526 rows) have no following bytes. **[H]** They
stand for 0.0 and the missing value. A reader should report them as special until
this is established.

**FPC block [E].** Literal doubles are compressed with FPC: M. Burtscher and P.
Ratanaworabhan, "FPC: A High-Speed Compressor for Double-Precision Floating-Point
Data," *IEEE Trans. Computers* 58(1), 2009. The predictors and hashes are as
published. Laid out here as:

- The first byte is the table size in bits (`0C`, 12 bits, in every block): both
  tables hold 2^12 entries, zero-initialized for each block.
- Codes and residuals are **interleaved**: one byte holds two 4-bit codes, low
  nibble first, and is followed directly by those two values' residuals. This
  differs from the block layout in the paper.
- In a code, the high bit selects the predictor (1 = DFCM, 0 = FCM). The low 3
  bits index the residual length `[0, 1, 2, 3, 5, 6, 7, 8]` bytes; there is no
  length 4. A residual is little-endian with its high zero bytes omitted.
- value = prediction XOR residual, on the 64-bit pattern.
- For an odd count, the last code byte's high nibble is unused and has no
  residual, unlike the paper's spurious value.

Evidence over the 705 payloads whose first token is a block of all `b` values, and
their 265177 values, as the share of decoded values with a short decimal form (at
most 9 significant digits):

| Parameters | Share |
|---|---|
| 12-bit tables, as above | 0.757 |
| any other table size tried (8, 10, 16, 20, 24 bits) | ≤ 0.141 |
| DFCM hash right-shift 48 instead of 40 | 0.127 |
| FCM hash left-shift 5 instead of 6 | 0.108 |
| opposite predictor bit | 0.023 |

Absence of overrun does not test these parameters, because residual lengths come
from the codes alone.

*Values are not yet verified against the reference application.* Public-fact
checks: a periodic-table sample's first column equals the IUPAC group of elements
Z = 1–54, 54 of 54, and its second is 1, 1, then runs of the period lengths.

**Do not derive an implementation from the authors' FPC source code.** It is
under a non-commercial license. Implement from this document and the paper.

### 2.7 String sections **[E]** / **[H]**

Integer columns refer to strings stored in LZ4-compressed sections, 48 in the
corpus:

```
#_MSER_STRINGS_PSET 05 01 leb(csize) 00 u32(usize) | LZ4 block
```

The LZ4 block starts right after `usize`, and decodes to exactly `usize` bytes in
48 of 48. `csize` is the block's length plus 4, for `usize`, in 48 of 48.
Decoded content:

```
77 11 11 11 | u16 2 | u16 1 | u32 4 | u32 count | u32 bytes | u32 rows | strings
```

The strings are NUL-separated. A column's integer minus 1 is the offset of that
row's string. All 48 integer columns land only on string starts (46 with `FF FF`
segments, 2 with only single-value segments), giving text such as `Albania`,
`Austria`. **[H]** A column uses the first section after its payload: that rule
gave these results, but it is not independently established. **[N]** the header
fields here named `count`, `bytes` and `rows`. The names are placeholders: `count`
equals the number of strings in only 4 of 48 sections, and `bytes` equals the
string bytes in 0 of 48.

### 2.8 Settings document **[E]** / **[N]**

Each of the 98 files has a UTF-8 XML document with an `<OriginStorage>` root,
reached by a zero-run token and a literal run, and well-formed once the run-length
layer is expanded up to its terminating NUL. Its direct children are named after
tools, 225 distinct names over the corpus. Some files hold further documents of
this kind later on. **[N]** how many, and their role.

**This is not established to be a record of operations on the project's data, and
the evidence is against it.** Fitting tools appear in bar-chart templates with no
fit, and input ranges name sheets not seen in the project. **[H]** It holds saved
per-tool settings carried with the file. Numeric fit fields exist in it; **[N]**
whether they describe the project's data, and where a real operation tree lives.

Values in this document can hold local file paths and usernames from other
machines. Do not copy them into tests, fixtures or documentation.

### 2.9 Column type and labels **[E]** / **[H]** / **[N]**

Later in the stream, each worksheet has a window record and curve records for its
columns. These are framed `80 <n> <n coded bytes>` like column records:

- A curve record expands to 519 bytes, or to 515 at format version 4.33xx.
- The short column name is NUL-terminated at offset `0x12`.
- The byte at `0x11` is the column type: 3 X, 0 Y, 5 Z, 6 X error, 2 Y error, 4
  label. This matches the open `liborigin` reader. Value 1 also occurs **[N]**.

After the frame come up to 3 filler bytes (seen before a label: `00`, `01 00`,
`01 00 0A`), then the label, if any: `<s> <s-1> <s-1 bytes ending in NUL>`, whose
text is the long name, units and comment on lines separated by CR LF, such as
`BE`, `eV`. `<s>` can equal a filler byte, so try the label form first at each
position. Otherwise another frame follows. Label text can hold Origin markup:
`2\g(q)`, `\+(o)`.

**Joining to a column [E] / [H].** Past the last column payload, a curve record
belongs to whichever book name occurs last before it, among the book names the
file's columns carry, as a literal followed by a NUL or a zero-run token. A column
named `<book>_<col>` then takes a record named `<col>` in its book. **[N]** A record
in a book with no columns would be attributed to the previous book. Measured: 885
of 903 columns reach a record in their book, 618 of them unique within it. Types
then read 632 Y, 139 X, 16 Z, 12 Y error, 8 of value 1.

**[H]** Repeated records in a book belong to its sheets in order (`<col>@2` is the
second). This fails where a book has fewer records than the sheet index (78), and
18 columns reach no record (at least 12 of them `@3`). **[N]** multi-sheet
structure. A reader must leave these columns without a type or label rather than
guess. Some parsed labels hold stray control bytes **[N]**.

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

1. **[N]** The meaning of compact-double codes `64` and `65` (§2.6).
2. **[N]** The flag byte's full meaning, including flag `3` (§2.6).
3. **[N]** The fields of the expanded column record, against the legacy header (§2.5).
4. **[N]** A walk of the whole run-length stream, and a record-type catalog (§2.1).
5. **[N]** Matrix storage: whether matrices share the column payload form.
6. **[N]** Where a real operation tree lives, if anywhere (§2.8).
7. **[N]** Object nesting and boundaries — the document tree.
8. **[N]** 36-byte asset-directory entries (§2.4).
9. **[N]** Legacy `.opj` delta vs. what existing open `.opj` readers cover.
