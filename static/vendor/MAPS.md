# Map libraries

These browser assets are served by B-Snap rather than fetched from CDNs at page
load. Versions match the previous Maps dependencies. Each package directory
includes its upstream license. `maps-manifest.json` records the pinned npm
archives and their SHA-256 checksums for future updates.

Keep the distributed CSS images and JavaScript source maps beside the files
that reference them. Leaflet is required; fullscreen, marker clustering and
heatmap plugins are optional, with basic camera markers available if they fail.

Map tiles still come from the selected OpenStreetMap/Esri provider. Bundling
the libraries does not provide offline map tiles or change their attribution.
