"""Markdown → PDF conversion service via WeasyPrint (HTML input)."""

from __future__ import annotations

from weasyprint import CSS, HTML

_PRINT_CSS = CSS(
    string="""
@page {
    margin: 2cm;
    size: A4;
}

body {
    font-family: 'Liberation Sans', Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}

h1, h2, h3, h4, h5, h6 {
    font-weight: 600;
    margin-top: 1.4em;
    margin-bottom: 0.5em;
    page-break-after: avoid;
}

h1 {
    font-size: 1.9em;
    border-bottom: 2px solid #e0e0e0;
    padding-bottom: 0.25em;
}

h2 {
    font-size: 1.5em;
    border-bottom: 1px solid #e0e0e0;
    padding-bottom: 0.2em;
}

h3 { font-size: 1.2em; }

p { margin: 0.4em 0 0.9em 0; }

code {
    font-family: 'Liberation Mono', 'Courier New', monospace;
    font-size: 0.88em;
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
}

pre {
    background: #f4f4f4;
    padding: 1em;
    border-radius: 4px;
    overflow: hidden;
    page-break-inside: avoid;
    margin: 0.5em 0 1em 0;
}

pre code {
    background: transparent;
    padding: 0;
    font-size: 0.85em;
}

/* Syntax-highlighting classes (highlight.js subset) */
.hljs             { background: #f4f4f4; color: #333; }
.hljs-keyword     { color: #a626a4; font-weight: bold; }
.hljs-string      { color: #50a14f; }
.hljs-comment     { color: #a0a0a0; font-style: italic; }
.hljs-number      { color: #986801; }
.hljs-built_in    { color: #c18401; }
.hljs-title       { color: #4078f2; }
.hljs-type        { color: #c18401; }
.hljs-attr        { color: #986801; }
.hljs-variable    { color: #e45649; }
.hljs-literal     { color: #0184bb; }
.hljs-tag         { color: #e45649; }
.hljs-name        { color: #e45649; }
.hljs-meta        { color: #9a6e3a; }
.hljs-selector-tag { color: #e45649; }
.hljs-selector-class { color: #986801; }
.hljs-selector-id { color: #986801; }
.hljs-operator    { color: #0184bb; }
.hljs-punctuation { color: #383a42; }

blockquote {
    border-left: 4px solid #d0d0d0;
    margin: 0 0 1em 0;
    padding: 0.5em 1em;
    color: #555;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 1em;
    page-break-inside: avoid;
}

th, td {
    border: 1px solid #d0d0d0;
    padding: 0.4em 0.8em;
    text-align: left;
}

th {
    background: #f0f0f0;
    font-weight: 600;
}

img, svg {
    max-width: 100%;
    height: auto;
}

a { color: #2563eb; }

hr {
    border: none;
    border-top: 1px solid #e0e0e0;
    margin: 1.5em 0;
}

ul, ol { padding-left: 1.5em; margin: 0.4em 0 0.9em 0; }
li { margin-bottom: 0.25em; }
"""
)


def convert_html_to_pdf(html: str) -> bytes:
    """Convert a full HTML document string to PDF bytes using WeasyPrint."""
    return HTML(string=html).write_pdf(stylesheets=[_PRINT_CSS])
