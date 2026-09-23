# M-Files vault schema for the eBook library

What `ebook-uploader.py` expects a vault to contain, written so someone with M-Files Admin
access can build the same structure in a different vault from scratch.

Everything below was read out of the live vault over MFWS
(`structure/objecttypes`, `structure/classes`, `structure/properties`, `valuelists`)
rather than transcribed from the code, so it describes what actually exists.

## The one rule that matters: names, not IDs

**The tool resolves every object type, class and property definition by *name*.** The IDs in
this document are the ones this vault happens to have assigned; a new vault will assign
different ones, and that is fine. What must match exactly is the **name**, including its
capitalisation and spacing.

`ebook-uploader.py` looks a custom property up by casefolded name *and* validates its data
type. A property that is missing is skipped with a warning and the upload continues without
that field; a property whose data type is wrong is also skipped. So a typo in a name does not
crash the run — it silently produces eBooks with that field empty. Check the warnings on the
first run against a new vault.

Object type and class names are supplied on the command line and default to the names used
here (`--object-type eBook`, `--object-class eBook`, `--author-type Author`,
`--publisher-type Publisher`, `--bundle-type "eBook bundle"`), so a vault that names them
differently works by passing the other names rather than by editing code.

## Object types

Four custom object types. All four are *real object types* (they hold objects, not just
value-list entries), and each one is its own value list, which is what lets an eBook point at
an Author object rather than at a loose string.

| ID | Name | Purpose |
|---:|---|---|
| 101 | `eBook` | The book itself. Holds the PDF file. |
| 102 | `Author` | One per person. An eBook links to many. |
| 103 | `Publisher` | One per publishing house. |
| 104 | `eBook bundle` | A collection a book belongs to, e.g. a bought bundle or a themed set. |

Creating an object type in M-Files Admin automatically creates, alongside it:

* a **value list** of the same ID and name (101–104 here), and
* two property definitions — `Owner (<name>)`, a single-select lookup, and `<name>`, a
  multi-select lookup, both based on that value list.

Those generated property definitions are the ones the eBook class actually uses, so **do not
create `Author`, `Publisher` or `eBook bundle` properties by hand** — make the object types
and M-Files will produce them. In this vault they came out as:

| ID | Name | Data type | Based on value list |
|---:|---|---|---:|
| 1026 | `Owner (eBook)` | Lookup | 101 |
| 1027 | `eBook` | Multi-select lookup | 101 |
| 1028 | `Owner (Author)` | Lookup | 102 |
| 1029 | `Author` | Multi-select lookup | 102 |
| 1030 | `Owner (Publisher)` | Lookup | 103 |
| 1031 | `Publisher` | Multi-select lookup | 103 |
| 1032 | `Owner (eBook bundle)` | Lookup | 104 |
| 1033 | `eBook bundle` | Multi-select lookup | 104 |

## Classes

One class per object type, each named after it. Only the `eBook` class carries custom
properties; the other three are plain — an Author object is just a name.

| Class ID | Name | Object type |
|---:|---|---:|
| 2 | `eBook` | 101 |
| 3 | `Author` | 102 |
| 4 | `Publisher` | 103 |
| 5 | `eBook bundle` | 104 |

## Property definitions to create by hand

These five are the only ones that need making in M-Files Admin. All are plain (not based on a
value list) and apply to all object types.

| Name | Data type | Holds |
|---|---|---|
| `ISBN` | Text | Every ISBN found in the book, comma-separated |
| `Publishing year` | Integer | Year of publication |
| `Page count` | Integer | Pages in the PDF |
| `Source SHA-1` | Text | SHA-1 of the uploaded file, lowercase hex |
| `Source` | Multi-line text | The metadata trail — see below |

> `Source` **must be multi-line text**, not text. A plain text property silently truncates at
> 100 characters (see Gotchas), which would cut every trail to its first line.

## The eBook class association

Associate these with class `eBook`. None are required — a book missing a publisher should
still upload.

| ID | Name | Data type | Required |
|---:|---|---|---|
| 0 | `Name or title` | Text | yes (built-in) |
| 1029 | `Author` | Multi-select lookup | no |
| 1031 | `Publisher` | Multi-select lookup | no |
| 1033 | `eBook bundle` | Multi-select lookup | no |
| 1034 | `ISBN` | Text | no |
| 1035 | `Publishing year` | Integer | no |
| 1036 | `Page count` | Integer | no |
| 1037 | `Source SHA-1` | Text | no |
| 1038 | `Source` | Multi-line text | no |

The remaining entries on the class (`Created`, `Last modified`, `Single file`,
`Class groups`, and so on) are M-Files built-ins that appear on every class and need no
setting up.

### Built-in properties the tool writes

| ID | Name | Note |
|---:|---|---|
| 0 | `Name or title` | The book's title, as enriched by the LLM |
| 26 | `Keywords` | Comma-separated subject keywords. **Written even though it is not associated with the class** — M-Files allows setting a property a class does not list, it just does not show in the class layout. Associate it if you want it visible by default. |
| 100 | `Class` | Set to the `eBook` class |
| 33 | `Comment` | **Legacy. Do not use.** See below. |

## What goes in `Source`

`Source` is the provenance trail: where each piece of metadata came from, so a wrong value can
be traced to whichever of Tika, the LLM or the command line produced it. Four line kinds, all
optional:

```
Publisher named in the eBook: Wiley Publishing, Inc.
ISBN: 0-471-23712-4
Keywords: social engineering, security's weakest link, human factor, deceptive practices
Original filename: Art of Deception Controlling the Human Element of Security, The - artofdeception.pdf
```

A fifth line, `Previous publisher in the vault: <name>`, is appended by `--repoint-publisher`
when it moves a book to a different publisher, recording what was displaced.

This lives in a custom property for a specific reason. It was originally written to M-Files'
predefined `Comment` (33), which is a **version** comment: it belongs to the version it was
written on and is *not* carried forward, so the trail vanished the moment anything else wrote
to the book. `Source` is ordinary metadata and is inherited by new versions. If a vault has no
`Source` property the tool falls back to `Comment` and logs a warning saying the trail will
not survive — a known limp, not a silent one.

## Gotchas worth knowing before you build this

* **Text properties truncate at 100 characters, silently.** No error, no warning — the value
  is just short. This applies to `ISBN` and `Source SHA-1` (both comfortably under) and is why
  `Source` must be multi-line text. The same cap applies to stored *file names*.
* **Multi-line text is stored with CRLF.** Write `\n` and it reads back `\r\n`. Normalise
  before comparing or every verification reports a false mismatch.
* **Lookups match by name, and M-Files folds some characters.** `ß` compares equal to `ss`, so
  two authors spelled differently can collide into one lookup entry. Expect occasional
  duplicate-looking merges in `Author`.
* **Object listings cap at 500 rows.** `objects?o=101` returns 500 with `MoreResults: true`
  and no other complaint. Pass `&limit=100000`; the `&l=` parameter is ignored.
* **The metadata structure is read-only over MFWS.** `POST /REST/structure/properties` answers
  HTTP 405. Every property definition here has to be created in M-Files Admin by a person;
  only then can a script verify it.
* **Windows `MAX_PATH`.** When the client's local path plus filename exceeds 260 characters
  the Windows client cannot open or download the file and throws a raw JSON error into the
  preview pane. Metadata still displays, and MFWS is unaffected, so this never breaks an
  upload — but long book titles plus long filenames reach it easily. M-Files has confirmed it
  as a server-side problem.

## Recreating this in a new vault

1. Create object types `eBook`, `Author`, `Publisher`, `eBook bundle`. Let M-Files generate
   each one's value list and lookup properties.
2. Create classes `eBook`, `Author`, `Publisher`, `eBook bundle`, each on its matching object
   type.
3. Create the five property definitions in the table above, minding `Source`'s data type.
4. Associate `Author`, `Publisher`, `eBook bundle`, `ISBN`, `Publishing year`, `Page count`,
   `Source SHA-1` and `Source` with the `eBook` class. Optionally associate `Keywords` (26).
5. Run `ebook-uploader.py` against a handful of books and read the startup log. It prints one
   `Storing <field> in property '<name>' (<id>)` line per resolved property; a field you
   expect and do not see is a name or data-type mismatch.

## Current contents of this vault

Read live, as a sense of scale rather than as part of the schema.

| Object type | Objects |
|---|---:|
| eBook | 1732 |
| Author | 2745 |
| Publisher | 37 |
| eBook bundle | 99 |
