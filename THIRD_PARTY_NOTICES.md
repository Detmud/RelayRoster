# Third-party notices

RelayRoster includes or is built on the following third-party software. Many thanks to their authors.

## In the user interface

### Tabler

- Project: <https://tabler.io>, source <https://github.com/tabler/tabler>
- Used: `tabler.min.css` from the npm package `@tabler/core` 1.7.1. It is not stored in this repository;
  `tools/fetch_vendor.py` downloads it during the build, checks a pinned SHA-256 and places it with this license as
  `app/static/vendor/LICENSE-tabler.txt`. No Tabler JavaScript is used.
- License: MIT

```
The MIT License (MIT)

Copyright (c) 2018-2026 The Tabler Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

The compiled CSS additionally carries the notice "Copyright 2018-2026 codecalm.net Paweł Kuna".

### Tabler Icons

- Project: <https://tabler.io/icons>, source <https://github.com/tabler/tabler-icons>
- Used: individual icons copied as inline SVG into `app/static/index.html`, `app/static/login.html` and
  `app/static/app.js`
- License: MIT

```
MIT License

Copyright (c) 2020-2026 Paweł Kuna

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
```

### Bootstrap

Tabler is based on [Bootstrap](https://getbootstrap.com) (MIT, Copyright (c) 2011-2026 The Bootstrap Authors), whose
styles are part of the compiled Tabler CSS.

## Python packages in the container image

Installed from PyPI during the build (`requirements.txt`); each package's license file is included in the image under
`site-packages/<package>-<version>.dist-info/`. Their own dependencies (e.g. Starlette, Pydantic, AnyIO) are installed
the same way.

| Package | License | Project |
|---|---|---|
| FastAPI | MIT | <https://github.com/fastapi/fastapi> |
| Uvicorn | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| HTTPX | BSD-3-Clause | <https://github.com/encode/httpx> |
| PyYAML | MIT | <https://github.com/yaml/pyyaml> |
| PyJWT | MIT | <https://github.com/jpadilla/pyjwt> |
| cryptography | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography> |
| ruamel.yaml | MIT | <https://sourceforge.net/projects/ruamel-yaml/> |

## Trademark

Shelly is a trademark of Shelly Europe Ltd. RelayRoster is an independent project and is not affiliated with,
endorsed by or sponsored by Shelly Europe Ltd.
