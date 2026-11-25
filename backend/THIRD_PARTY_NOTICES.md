# Third-party notices

## OpenShorts `subtitles.py`

Source: `openshorts/subtitles.py`, commit `89f030934388d5167b3b0ede164ef893dad09ae9`.
`app/montage_packaging.py` adapts only the ASS v4+ header, styles, event
structure and explicit positioning. It does not use the cloud/YOLO paths,
Verdana fallback, time rounding semantics, or the source's user-text rewrite.

Copyright (c) 2024 OpenShorts. MIT License (the full MIT terms below apply).

## Noto Sans CJK SC Regular

Source: Noto CJK commit `f8d157532fbfaeda587e826d4cd5b21a49186f7c`, file
`NotoSansCJKsc-Regular.otf`; version `Version 2.004;hotconv 1.0.118;makeotfexe
2.5.65603`; SHA-256
`2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`.
Copyright © 2014–2021 Adobe. Licensed under SIL Open Font License 1.1; the
complete license accompanies the asset at `app/fonts/NotoSans-LICENSE`.

## OpenShorts `reframe_v2.py`

Source: `openshorts/reframe_v2.py`, commit `89f030934388d5167b3b0ede164ef893dad09ae9`.
The adapted `dedupe_sendcmd_lines` helper in `app/montage_reframe.py` retains
the original change-point algorithm and is extended only by calling it for
both coordinates and unique crop instances. Cloud code and YOLO code were not
copied or used.

Copyright (c) 2024 OpenShorts

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
