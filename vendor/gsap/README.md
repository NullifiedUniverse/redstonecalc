# GSAP, vendored

`gsap.min.js` and `ScrollTrigger.min.js`, version 3.15.0, copied verbatim from
the `gsap` npm package.

They are **in the repository rather than fetched** for one reason: `docs/preview.html`
is published as an artifact, and the artifact runtime serves it under a strict
content-security policy that blocks every external host. A `<script src>` pointing
at a CDN does not fail loudly there — it fails silently, and the page arrives with
no animation and no error anyone will see. `tools/build_pages.py` inlines both
files into the page, the same way it inlines the redstone engine, so the page
stays a single self-contained file with no request to anywhere.

GSAP is distributed under GreenSock's standard "no charge" licence
(<https://gsap.com/standard-license>), which covers this use.

To update: `npm install gsap && cp node_modules/gsap/dist/{gsap,ScrollTrigger}.min.js vendor/gsap/`
