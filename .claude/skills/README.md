# Caveman skills

Skills vendored from [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)
(commit `15581d14007fd01fb3f132016741962f34936ca2`), MIT-licensed (`skills/` per
that repo's `LICENSING.md`).

Copyright (c) 2026 Julius Brussee. Permission is hereby granted, free of
charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to
the following conditions: the above copyright notice and this permission
notice shall be included in all copies or substantial portions of the
Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.

## What's excluded, and why

`caveman-setup` is **not** included here. It wires a repository through the
external Caveman Cloud gateway (an outbound proxy that all LLM traffic would
route through) — a network/infrastructure change, unlike every other skill
here, which is a self-contained prompt or local script that only acts when
explicitly invoked. It was left out on request; add it manually if you
decide you want that integration after reviewing what it does.

Everything else in `skills/` upstream (prompt-only or local-script skills)
is included as-is.
