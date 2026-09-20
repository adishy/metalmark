/*
 * Turns the metalmark into the icon set `frontend/public/` ships.
 *
 *     npm run icons
 *
 * Run it whenever `metalmark-mark.mjs` changes. The generated files are
 * committed — a favicon that only exists after someone remembers to run a build
 * step is a favicon that is missing from the repo — so the point of this script
 * is that the committed PNGs are reproducible rather than mystery binaries. The
 * alternative, a hand-drawn PNG, cannot be re-derived at a new size or checked
 * by reading, which is exactly what decision F was avoiding.
 *
 * ---------------------------------------------------------------------------
 * Why `sharp`, and why the downscaling is done this way
 * ---------------------------------------------------------------------------
 *
 * There is no rasteriser in a plain Node install, and writing one (a bezier
 * flattener plus an anti-aliased scanline fill) would be several hundred lines
 * of geometry whose bugs would show up as a slightly wrong favicon — a bad
 * trade. `sharp` (libvips) is a devDependency and is never loaded at runtime:
 * production ships the PNGs, not the rasteriser.
 *
 * Every size is rendered by *halving* down from a 1024 px master rather than in
 * one jump. A single 1024 → 16 resize reads the whole neighbourhood of each
 * output pixel and can ring against the wing edges, which is precisely the size
 * where the mark has no room for it. Halving keeps the kernel's radius small
 * relative to the image at every step.
 *
 * The `.ico` is assembled here because sharp does not write one. The container
 * is 6 bytes of header plus a 16-byte directory entry per image, and the images
 * are PNG rather than BMP — supported by every browser that matters, and the
 * reason this stays ~40 readable lines instead of a BMP encoder.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import { markSvg } from "./metalmark-mark.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = join(HERE, "..", "public");

/** The master render. Everything else is a halving chain down from it. */
const BASE = 1024;

/** Render the SVG at BASE×BASE, then step down to `size`. */
async function toPng(svg, size) {
  let current = await sharp(Buffer.from(svg), { density: (72 * BASE) / 64 })
    .resize(BASE, BASE, { fit: "fill" })
    .png()
    .toBuffer();
  let currentSize = BASE;
  while (currentSize / 2 >= size) {
    currentSize /= 2;
    current = await sharp(current).resize(currentSize, currentSize, { kernel: "lanczos3" }).png().toBuffer();
  }
  if (currentSize !== size) {
    current = await sharp(current).resize(size, size, { kernel: "lanczos3" }).png().toBuffer();
  }
  return current;
}

/**
 * An ICO container holding PNG payloads.
 *
 * The directory entry's width and height are single bytes, so 256 is written as
 * 0 — the one place this format is less than obvious.
 */
function toIco(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // 1 = icon (2 would be a cursor)
  header.writeUInt16LE(images.length, 4);

  const directory = Buffer.alloc(16 * images.length);
  let offset = 6 + directory.length;
  images.forEach(({ size, data }, i) => {
    const at = i * 16;
    directory.writeUInt8(size >= 256 ? 0 : size, at);
    directory.writeUInt8(size >= 256 ? 0 : size, at + 1);
    directory.writeUInt8(0, at + 2); // palette size: 0 for true colour
    directory.writeUInt8(0, at + 3); // reserved
    directory.writeUInt16LE(1, at + 4); // colour planes
    directory.writeUInt16LE(32, at + 6); // bits per pixel
    directory.writeUInt32LE(data.length, at + 8);
    directory.writeUInt32LE(offset, at + 12);
    offset += data.length;
  });

  return Buffer.concat([header, directory, ...images.map((i) => i.data)]);
}

async function main() {
  await mkdir(PUBLIC, { recursive: true });

  /*
   * Three variants of the one mark, differing only in what happens at the edge:
   *
   *   - rounded: a browser tab, a desktop shortcut, a PWA "any" icon. Stands on
   *     its own, so it owns its corners.
   *   - bleed: `apple-touch-icon`. iOS applies its own rounded mask to a square
   *     source, so shipping pre-rounded corners there double-rounds and leaks
   *     the page background through the gap between the two curves.
   *   - maskable: the PWA maskable icons. Full bleed, with the artwork pulled
   *     into the safe circle the platform's mask guarantees, so a launcher that
   *     crops to a circle or a squircle never clips a wing tip.
   */
  const rounded = markSvg();
  const bleed = markSvg({ bleed: true });
  const maskable = markSvg({ maskable: true, bleed: true });

  const written = [];

  async function emit(name, buffer, variant, size) {
    await writeFile(join(PUBLIC, name), buffer);
    written.push({ name, bytes: buffer.length, size, variant });
  }

  await emit("favicon.svg", Buffer.from(rounded, "utf8"), "rounded", "vector");

  const icoSizes = [16, 32, 48];
  const icoImages = [];
  for (const size of icoSizes) icoImages.push({ size, data: await toPng(rounded, size) });
  await emit("favicon.ico", toIco(icoImages), "rounded", icoSizes.join("/"));

  await emit("apple-touch-icon.png", await toPng(bleed, 180), "bleed", 180);

  for (const size of [192, 512]) {
    await emit(`icon-${size}.png`, await toPng(rounded, size), "rounded", size);
    await emit(`icon-maskable-${size}.png`, await toPng(maskable, size), "maskable", size);
  }

  // For the desktop-notification feature that will land later (decision E:
  // "the notification icon is the app icon"). Nothing sets this yet; it exists
  // so that feature does not have to invent a second mark under deadline.
  await emit("notification-icon.png", await toPng(rounded, 256), "rounded", 256);

  const width = Math.max(...written.map((w) => w.name.length));
  for (const { name, bytes, size, variant } of written) {
    console.log(`${name.padEnd(width)}  ${String(size).padStart(7)}px  ${variant.padEnd(7)}  ${(bytes / 1024).toFixed(1)} KiB`);
  }
  console.log(`\n${written.length} files in ${PUBLIC}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
