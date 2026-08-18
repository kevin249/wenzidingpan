#!/usr/bin/env node
/**
 * Generates assets/tray.png (32x32 RGBA) without any third-party image library.
 * A rounded accent-coloured tile with a white rising line, so the glyph stays
 * legible on both light and dark system trays.
 */
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const SIZE = 32;
const px = Buffer.alloc(SIZE * SIZE * 4, 0);

function set(x, y, [r, g, b], a = 255) {
  if (x < 0 || y < 0 || x >= SIZE || y >= SIZE) return;
  const i = (y * SIZE + x) * 4;
  const src = a / 255;
  const dst = px[i + 3] / 255;
  const out = src + dst * (1 - src);
  if (out === 0) return;
  px[i] = Math.round((r * src + px[i] * dst * (1 - src)) / out);
  px[i + 1] = Math.round((g * src + px[i + 1] * dst * (1 - src)) / out);
  px[i + 2] = Math.round((b * src + px[i + 2] * dst * (1 - src)) / out);
  px[i + 3] = Math.round(out * 255);
}

// Rounded tile.
const ACCENT = [34, 197, 94];
const RADIUS = 7;
for (let y = 0; y < SIZE; y++) {
  for (let x = 0; x < SIZE; x++) {
    const cx = Math.min(Math.max(x, RADIUS), SIZE - 1 - RADIUS);
    const cy = Math.min(Math.max(y, RADIUS), SIZE - 1 - RADIUS);
    const d = Math.hypot(x - cx, y - cy);
    if (d <= RADIUS - 0.5) set(x, y, ACCENT);
    else if (d < RADIUS + 0.5) set(x, y, ACCENT, Math.round((RADIUS + 0.5 - d) * 255));
  }
}

// Rising polyline.
const WHITE = [255, 255, 255];
const points = [[7, 23], [13, 16], [18, 20], [25, 9]];
function dot(x, y, r) {
  for (let dy = -r; dy <= r; dy++) {
    for (let dx = -r; dx <= r; dx++) {
      if (Math.hypot(dx, dy) <= r) set(Math.round(x + dx), Math.round(y + dy), WHITE);
    }
  }
}
for (let i = 0; i < points.length - 1; i++) {
  const [x0, y0] = points[i];
  const [x1, y1] = points[i + 1];
  const steps = Math.ceil(Math.hypot(x1 - x0, y1 - y0) * 4);
  for (let s = 0; s <= steps; s++) {
    dot(x0 + ((x1 - x0) * s) / steps, y0 + ((y1 - y0) * s) / steps, 1.4);
  }
}
// Arrow head at the top right.
for (let i = 0; i < 7; i++) {
  dot(25 - i, 9, 1.2);
  dot(25, 9 + i, 1.2);
}

// Encode as PNG.
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body) >>> 0);
  return Buffer.concat([len, body, crc]);
}
const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();
function crc32(buf) {
  let c = -1;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return c ^ -1;
}

const raw = Buffer.alloc((SIZE * 4 + 1) * SIZE);
for (let y = 0; y < SIZE; y++) {
  raw[y * (SIZE * 4 + 1)] = 0; // filter: none
  px.copy(raw, y * (SIZE * 4 + 1) + 1, y * SIZE * 4, (y + 1) * SIZE * 4);
}
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(SIZE, 0);
ihdr.writeUInt32BE(SIZE, 4);
ihdr[8] = 8; // bit depth
ihdr[9] = 6; // colour type RGBA
const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  chunk('IHDR', ihdr),
  chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
  chunk('IEND', Buffer.alloc(0)),
]);

const out = path.join(__dirname, '..', 'assets', 'tray.png');
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, png);
console.log(`wrote ${out} (${png.length} bytes)`);
