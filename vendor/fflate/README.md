# fflate

`fflate.umd.js` is fflate's UMD build, vendored rather than fetched, for the
same reason `vendor/gsap` is: `docs/preview.html` ships as an artifact, and the
artifact runtime blocks every external host. A `<script src>` to a CDN there
does not fail loudly — the page arrives without it and without an error anyone
will see.

It is used for one thing: assembling a Minecraft datapack in the browser so a
reader can take the machine they have just been operating and put it in their
own world. A datapack is a zip of `pack.mcmeta` plus a tree of `.mcfunction`
files, and writing a correct zip by hand is a CRC table and three header
formats — a real library is the right answer, and fflate is about a third the
size of the obvious alternative.

Upstream: https://github.com/101arrowz/fflate (MIT)
