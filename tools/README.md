# tools

## `pinecheck.py`
A pre-flight check for the Pine files in `pinescript/`, for use when a TradingView
compiler is not to hand.

```
python3 tools/pinecheck.py pinescript/*.pine
```

It reports **undeclared identifiers** — the `CE10272` class of error — by stripping
comments and string literals with a real character scanner and diffing the identifiers
used against those declared. Exit code is non-zero if anything is found.

The scanner matters: an earlier regex version stripped `'...'` before `"..."`, so an
apostrophe inside a double-quoted tooltip (`"the table's 75% line"`) swallowed the rest
of the file and the check silently passed on broken code. That is how a missing
declaration reached a release.

It does **not** model Pine's scopes, so it cannot check declaration order — function
parameters and block-locals legitimately reuse names that are also global. Existence is
the part that is worth automating; order still needs the real compiler.
