/**
 * obj_preview.mjs
 * رندر پیش‌نمایش ساده سه‌بعدی (Front + Back) از یک فایل OBJ + تکسچر
 * بدون هیچ وابستگی native — فقط Node.js خالص (zlib برای PNG)
 *
 * استفاده:
 *   node obj_preview.mjs <input.obj> <texture.png> <output_preview.png> [size]
 */

import fs from "fs";
import path from "path";
import zlib from "zlib";

const [, , objPath, texturePath, outPath, sizeArg] = process.argv;
if (!objPath || !texturePath || !outPath) {
  console.error("Usage: node obj_preview.mjs <input.obj> <texture.png> <output.png> [size]");
  process.exit(1);
}

const VIEW_SIZE = parseInt(sizeArg || "512", 10); // اندازه هر پنل (جلو/پشت)
const PADDING = 24;     // فاصله بین دو پنل
const MARGIN = 20;      // حاشیه دور تصویر

// ─────────────────────────────────────────────────────────────────────────
// 1) خواندن و پارس PNG تکسچر (decoder خام PNG، فقط RGBA/RGB غیر-interlaced)
// ─────────────────────────────────────────────────────────────────────────

function readPNG(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error("Not a valid PNG signature");
  let offset = 8;
  let width = 0, height = 0, bitDepth = 0, colorType = 0;
  let idatChunks = [];
  let palette = null;
  let trns = null;

  while (offset < buf.length) {
    const len = buf.readUInt32BE(offset);
    const type = buf.toString("ascii", offset + 4, offset + 8);
    const dataStart = offset + 8;
    const data = buf.slice(dataStart, dataStart + len);

    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data.readUInt8(8);
      colorType = data.readUInt8(9);
      const interlace = data.readUInt8(12);
      if (interlace !== 0) throw new Error("Interlaced PNG not supported");
      if (bitDepth !== 8) throw new Error(`Bit depth ${bitDepth} not supported (only 8)`);
    } else if (type === "PLTE") {
      palette = data;
    } else if (type === "tRNS") {
      trns = data;
    } else if (type === "IDAT") {
      idatChunks.push(data);
    } else if (type === "IEND") {
      break;
    }
    offset = dataStart + len + 4; // +4 for CRC
  }

  const raw = zlib.inflateSync(Buffer.concat(idatChunks));

  let channels;
  if (colorType === 2) channels = 3;       // RGB
  else if (colorType === 6) channels = 4;  // RGBA
  else if (colorType === 0) channels = 1;  // Grayscale
  else if (colorType === 3) channels = 1;  // Palette (index per pixel)
  else throw new Error(`Color type ${colorType} not supported`);

  const bytesPerPixel = channels;
  const stride = width * bytesPerPixel;

  // un-filter (defilter) scanlines
  const out = Buffer.alloc(height * stride);
  let rawOffset = 0;
  let prevLine = Buffer.alloc(stride);

  for (let y = 0; y < height; y++) {
    const filterType = raw[rawOffset];
    rawOffset += 1;
    const line = raw.slice(rawOffset, rawOffset + stride);
    rawOffset += stride;
    const outLine = Buffer.alloc(stride);

    for (let x = 0; x < stride; x++) {
      const a = x >= bytesPerPixel ? outLine[x - bytesPerPixel] : 0;
      const b = prevLine[x];
      const c = x >= bytesPerPixel ? prevLine[x - bytesPerPixel] : 0;
      let val = line[x];

      if (filterType === 0) {
        // None
      } else if (filterType === 1) {
        val = (val + a) & 0xff;
      } else if (filterType === 2) {
        val = (val + b) & 0xff;
      } else if (filterType === 3) {
        val = (val + Math.floor((a + b) / 2)) & 0xff;
      } else if (filterType === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        const pr = (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
        val = (val + pr) & 0xff;
      } else {
        throw new Error(`Unknown filter type ${filterType}`);
      }
      outLine[x] = val;
    }
    outLine.copy(out, y * stride);
    prevLine = outLine;
  }

  // تبدیل به RGBA یکنواخت
  const rgba = Buffer.alloc(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    let r, g, b, a = 255;
    if (colorType === 2) {
      r = out[i * 3]; g = out[i * 3 + 1]; b = out[i * 3 + 2];
    } else if (colorType === 6) {
      r = out[i * 4]; g = out[i * 4 + 1]; b = out[i * 4 + 2]; a = out[i * 4 + 3];
    } else if (colorType === 0) {
      r = g = b = out[i];
    } else if (colorType === 3) {
      const idx = out[i];
      r = palette[idx * 3]; g = palette[idx * 3 + 1]; b = palette[idx * 3 + 2];
      a = trns && idx < trns.length ? trns[idx] : 255;
    }
    rgba[i * 4] = r; rgba[i * 4 + 1] = g; rgba[i * 4 + 2] = b; rgba[i * 4 + 3] = a;
  }

  return { width, height, data: rgba };
}

// ─────────────────────────────────────────────────────────────────────────
// 2) نوشتن PNG خروجی (RGBA 8-bit، بدون فیلتر/فیلتر None)
// ─────────────────────────────────────────────────────────────────────────

function writePNG(filePath, width, height, rgba) {
  function crc32(buf) {
    let c, crc = 0xffffffff;
    for (let i = 0; i < buf.length; i++) {
      c = (crc ^ buf[i]) & 0xff;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      crc = (crc >>> 8) ^ c;
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  function chunk(type, data) {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length, 0);
    const typeBuf = Buffer.from(type, "ascii");
    const crcBuf = Buffer.alloc(4);
    crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
    return Buffer.concat([len, typeBuf, data, crcBuf]);
  }

  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 6;   // color type RGBA
  ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;

  // scanlines with filter byte 0 (None)
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0;
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, y * stride + stride);
  }
  const idatData = zlib.deflateSync(raw, { level: 9 });

  const png = Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", idatData),
    chunk("IEND", Buffer.alloc(0)),
  ]);

  fs.writeFileSync(filePath, png);
}

// ─────────────────────────────────────────────────────────────────────────
// 3) پارس OBJ
// ─────────────────────────────────────────────────────────────────────────

function parseOBJ(filePath) {
  const lines = fs.readFileSync(filePath, "utf-8").split("\n");
  const v = [], vt = [], vn = [];
  const faces = []; // { v:[i,i,i], t:[i,i,i], n:[i,i,i] }

  for (const lineRaw of lines) {
    const line = lineRaw.trim();
    if (!line || line.startsWith("#")) continue;
    const parts = line.split(/\s+/);
    const cmd = parts[0];

    if (cmd === "v") {
      v.push([parseFloat(parts[1]), parseFloat(parts[2]), parseFloat(parts[3])]);
    } else if (cmd === "vt") {
      vt.push([parseFloat(parts[1]), parseFloat(parts[2])]);
    } else if (cmd === "vn") {
      vn.push([parseFloat(parts[1]), parseFloat(parts[2]), parseFloat(parts[3])]);
    } else if (cmd === "f") {
      const verts = parts.slice(1).map(p => {
        const [vi, ti, ni] = p.split("/");
        return {
          v: vi ? parseInt(vi, 10) - 1 : -1,
          t: ti ? parseInt(ti, 10) - 1 : -1,
          n: ni ? parseInt(ni, 10) - 1 : -1,
        };
      });
      // OBJ ممکنه quad یا polygon باشه؛ fan triangulation
      for (let i = 1; i < verts.length - 1; i++) {
        faces.push([verts[0], verts[i], verts[i + 1]]);
      }
    }
  }

  return { v, vt, vn, faces };
}

// ─────────────────────────────────────────────────────────────────────────
// 4) رندر یک نمای orthographic با z-buffer + texture sampling + flat shading
// ─────────────────────────────────────────────────────────────────────────

function renderViewFixed(model, texture, viewSize, viewDir) {
  const { v, vt, faces } = model;

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (const [x, y, z] of v) {
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
    if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
  }
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const cz = (minZ + maxZ) / 2;
  const extent = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 1e-6);

  const fb = new Float32Array(viewSize * viewSize * 4);
  // نزدیک‌ترین به دوربین = بزرگ‌ترین depth (چون zFlip باعث میشه نزدیک‌تر = z بزرگ‌تر)
  const zbuf = new Float32Array(viewSize * viewSize).fill(-Infinity);
  const hit = new Uint8Array(viewSize * viewSize); // آیا پیکسل پوشش داده شده

  const scale = (viewSize * 0.78) / extent;
  const mirrorX = viewDir === "back" ? -1 : 1;
  const zFlip = viewDir === "back" ? -1 : 1;

  function project(p) {
    const x = (p[0] - cx) * mirrorX;
    const y = (p[1] - cy);
    const z = (p[2] - cz) * zFlip;
    const sx = viewSize / 2 + x * scale;
    const sy = viewSize / 2 - y * scale;
    return [sx, sy, z];
  }

  function sampleTexture(u, vCoord) {
    let uu = u - Math.floor(u);
    let vv = vCoord - Math.floor(vCoord);
    if (!isFinite(uu)) uu = 0;
    if (!isFinite(vv)) vv = 0;
    let px = Math.min(texture.width - 1, Math.max(0, Math.floor(uu * texture.width)));
    let py = Math.min(texture.height - 1, Math.max(0, Math.floor((1 - vv) * texture.height)));
    const idx = (py * texture.width + px) * 4;
    return [texture.data[idx], texture.data[idx + 1], texture.data[idx + 2], texture.data[idx + 3]];
  }

  for (const tri of faces) {
    const p0 = v[tri[0].v], p1 = v[tri[1].v], p2 = v[tri[2].v];
    if (!p0 || !p1 || !p2) continue;

    const ax = p1[0] - p0[0], ay = p1[1] - p0[1], az = p1[2] - p0[2];
    const bx = p2[0] - p0[0], by = p2[1] - p0[1], bz = p2[2] - p0[2];
    let nx = ay * bz - az * by;
    let ny = az * bx - ax * bz;
    let nz = ax * by - ay * bx;
    const nlen = Math.hypot(nx, ny, nz) || 1;
    nx /= nlen; ny /= nlen; nz /= nlen;

    let lightDot = ny * 0.25 + nz * (viewDir === "back" ? -1 : 1);
    let brightness = 0.55 + 0.45 * Math.max(0, lightDot);
    if (brightness > 1) brightness = 1;
    brightness = Math.max(brightness, 0.35);

    const P0 = project(p0), P1 = project(p1), P2 = project(p2);

    const uvIdx = [tri[0].t, tri[1].t, tri[2].t];
    const hasUV = uvIdx.every(i => i >= 0 && vt[i]);
    const uv0 = hasUV ? vt[uvIdx[0]] : [0, 0];
    const uv1 = hasUV ? vt[uvIdx[1]] : [0, 0];
    const uv2 = hasUV ? vt[uvIdx[2]] : [0, 0];

    const minSX = Math.max(0, Math.floor(Math.min(P0[0], P1[0], P2[0])));
    const maxSX = Math.min(viewSize - 1, Math.ceil(Math.max(P0[0], P1[0], P2[0])));
    const minSY = Math.max(0, Math.floor(Math.min(P0[1], P1[1], P2[1])));
    const maxSY = Math.min(viewSize - 1, Math.ceil(Math.max(P0[1], P1[1], P2[1])));
    if (minSX > maxSX || minSY > maxSY) continue;

    const area = (P1[0] - P0[0]) * (P2[1] - P0[1]) - (P2[0] - P0[0]) * (P1[1] - P0[1]);
    if (Math.abs(area) < 1e-9) continue;

    for (let py = minSY; py <= maxSY; py++) {
      for (let px = minSX; px <= maxSX; px++) {
        const sx = px + 0.5, sy = py + 0.5;
        const w0 = ((P1[0] - sx) * (P2[1] - sy) - (P2[0] - sx) * (P1[1] - sy)) / area;
        const w1 = ((P2[0] - sx) * (P0[1] - sy) - (P0[0] - sx) * (P2[1] - sy)) / area;
        const w2 = 1 - w0 - w1;
        if (w0 < -0.0001 || w1 < -0.0001 || w2 < -0.0001) continue;

        const depth = w0 * P0[2] + w1 * P1[2] + w2 * P2[2];
        const zi = py * viewSize + px;
        if (depth <= zbuf[zi]) continue; // فقط نزدیک‌تر (بزرگ‌تر) رو نگه دار
        zbuf[zi] = depth;
        hit[zi] = 1;

        let r = 200, g = 200, b = 200, a = 255;
        if (hasUV) {
          const u = w0 * uv0[0] + w1 * uv1[0] + w2 * uv2[0];
          const vc = w0 * uv0[1] + w1 * uv1[1] + w2 * uv2[1];
          [r, g, b, a] = sampleTexture(u, vc);
        }
        if (a < 10) { hit[zi] = 0; continue; } // پیکسل کاملا شفاف رو رد کن

        fb[zi * 4] = r * brightness;
        fb[zi * 4 + 1] = g * brightness;
        fb[zi * 4 + 2] = b * brightness;
        fb[zi * 4 + 3] = a;
      }
    }
  }

  return { fb, hit };
}

// ─────────────────────────────────────────────────────────────────────────
// 5) ترکیب دو پنل (front/back) در یک تصویر نهایی با پس‌زمینه و لیبل
// ─────────────────────────────────────────────────────────────────────────

function compose(frontRes, backRes, viewSize) {
  const totalW = viewSize * 2 + PADDING + MARGIN * 2;
  const totalH = viewSize + MARGIN * 2 + 36; // فضای پایین برای لیبل متنی ساده (نواری رنگی، نه فونت واقعی)
  const out = new Uint8Array(totalW * totalH * 4);

  // پس‌زمینه: گرادیان خاکستری ملایم (شبیه استودیو)
  for (let y = 0; y < totalH; y++) {
    const t = y / totalH;
    const shade = Math.round(235 - t * 30);
    for (let x = 0; x < totalW; x++) {
      const idx = (y * totalW + x) * 4;
      out[idx] = shade; out[idx + 1] = shade; out[idx + 2] = shade; out[idx + 3] = 255;
    }
  }

  function blit(res, offsetX, offsetY) {
    const { fb, hit } = res;
    for (let y = 0; y < viewSize; y++) {
      for (let x = 0; x < viewSize; x++) {
        const si = y * viewSize + x;
        if (!hit[si]) continue;
        const dx = offsetX + x, dy = offsetY + y;
        if (dx < 0 || dx >= totalW || dy < 0 || dy >= totalH) continue;
        const di = (dy * totalW + dx) * 4;
        const sr = fb[si * 4], sg = fb[si * 4 + 1], sb = fb[si * 4 + 2], sa = fb[si * 4 + 3] / 255;
        // alpha blend روی پس‌زمینه
        out[di] = Math.round(sr * sa + out[di] * (1 - sa));
        out[di + 1] = Math.round(sg * sa + out[di + 1] * (1 - sa));
        out[di + 2] = Math.round(sb * sa + out[di + 2] * (1 - sa));
        out[di + 3] = 255;
      }
    }
  }

  blit(frontRes, MARGIN, MARGIN);
  blit(backRes, MARGIN + viewSize + PADDING, MARGIN);

  // خط جداکننده باریک وسط
  const sepX = MARGIN + viewSize + Math.floor(PADDING / 2);
  for (let y = MARGIN; y < MARGIN + viewSize; y++) {
    const idx = (y * totalW + sepX) * 4;
    out[idx] = 180; out[idx + 1] = 180; out[idx + 2] = 180; out[idx + 3] = 255;
  }

  return { width: totalW, height: totalH, data: Buffer.from(out) };
}

// ─────────────────────────────────────────────────────────────────────────
// اجرا
// ─────────────────────────────────────────────────────────────────────────

console.log("📥 خواندن مدل OBJ ...");
const model = parseOBJ(objPath);
console.log(`   Vertices: ${model.v.length}  Faces(tri): ${model.faces.length}`);

console.log("🖼  خواندن تکسچر ...");
const texture = readPNG(texturePath);
console.log(`   Texture: ${texture.width}x${texture.height}`);

console.log("🎨 رندر نمای جلو (front) ...");
const front = renderViewFixed(model, texture, VIEW_SIZE, "front");

console.log("🎨 رندر نمای پشت (back) ...");
const back = renderViewFixed(model, texture, VIEW_SIZE, "back");

console.log("🧩 ترکیب نهایی ...");
const finalImg = compose(front, back, VIEW_SIZE);

writePNG(outPath, finalImg.width, finalImg.height, finalImg.data);
console.log(`✅ پیش‌نمایش ساخته شد: ${outPath}`);
