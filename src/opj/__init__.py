"""opj — an independent, compatible reader for Origin project files.

Public API:
    read(path)            parse a file into a Document
    parse(data)           parse an in-memory buffer into a Document
    expand(data)          expand the run-length layer into logical bytes
    Header, Record, Document, OpjError, RunLengthError
    GlobalHeader, AssetDirectory, AssetEntry, StringSection
    Column, Segment, Special, SPECIAL_64, SPECIAL_65, Undecoded, Value
"""

from .opju import (
    SPECIAL_64,
    SPECIAL_65,
    AssetDirectory,
    AssetEntry,
    Column,
    Document,
    GlobalHeader,
    Header,
    OpjError,
    Record,
    RunLengthError,
    Segment,
    Special,
    StringSection,
    Undecoded,
    Value,
    expand,
    parse,
    read,
)

__all__ = [
    "SPECIAL_64",
    "SPECIAL_65",
    "AssetDirectory",
    "AssetEntry",
    "Column",
    "Document",
    "GlobalHeader",
    "Header",
    "OpjError",
    "Record",
    "RunLengthError",
    "Segment",
    "Special",
    "StringSection",
    "Undecoded",
    "Value",
    "expand",
    "parse",
    "read",
]

__version__ = "0.0.1"
