/**
 * block_render.mjs
 * رندر یک آیکون ایزومتریک (شبیه نمای اینونتوری) از یک OBJ چندمتریالی (چند تکسچر،
 * هر وجه با تکسچر خودش) — برای بلاک‌هایی مثل Command Block که هر جهتش یک
 * تکسچر جدا دارد، و هم برای بلاک‌های ساده مثل Dirt که یک تکسچر روی هر ۶ وجه دارد.
 *
 * برخلاف obj_preview.mjs (که فقط یک تکسچر ورودی می‌گیرد)، این اسکریپت:
 *   1) فایل .mtl کنار OBJ را می‌خواند و نگاشت «متریال → فایل تکسچر» را می‌سازد.
 *   2) همه‌ی تکسچرهای ارجاع‌شده را (هرکدام می‌تواند فایل جدا باشد) با sharp می‌خواند.
 *   3) هر مثلث OBJ را با تکسچر متریال خودش رندر می‌کند (نه یک تکسچر مشترک).
 *   4) یک نمای ایزومتریک تک (مثل آیکون اینونتوری ماینکرافت) تولید می‌کند.
 *
 * استفاده:
 *   node block_render.mjs <input.obj> <output.png> [size]
 * (فایل .mtl باید هم‌نام OBJ و در همان پوشه باشد؛ تکسچرها هم باید در همان پوشه باشند.)
 */

import fs from "fs";
import path from "path";
import sharp from "sharp";

const [, , objPath, outPath, sizeArg] = process.argv;
if (!objPath || !outPath) {
  console.error("Usage: node block_render.mjs <input.obj> <output.png> [size]");
  process.exit(1);
}

const SIZE = parseInt(sizeArg || "512", 10);
const dir = path.dirname(objPath);
const baseName = path.basename(objPath, ".obj");
const mtlPath = path.join(dir, baseName + ".mtl");

// ─── خواندن MTL ──────────────────────────────────────────────────────────────
function parseMTL(p) {
  const map = {};   // matName -> textureFileName
  const tints = {};  // matName -> [r,g,b] هرکدوم 0..1 (برای وجه‌هایی مثل بالای grass_block)
  if (!fs.existsSync(p)) return { map, tints };
  const lines = fs.readFileSync(p, "utf-8").split("\n");
  let current = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("newmtl ")) current = line.slice(7).trim();
    else if (line.startsWith("map_Kd ") && current) map[current] = line.slice(7).trim();
    else if (line.startsWith("# tint ") && current) {
      // خط غیراستاندارد که bot.py برای وجه‌های biome-tinted (مثل grass_block_top) می‌نویسه
      const parts = line.slice(7).trim().split(/\s+/).map(Number);
      if (parts.length === 3 && parts.every(n => isFinite(n))) {
        tints[current] = [parts[0] / 255, parts[1] / 255, parts[2] / 255];
      }
    }
  }
  return { map, tints };
}

const { map: matToTexFile, tints: matTint } = parseMTL(mtlPath);

// ─── خواندن OBJ (با آگاهی از usemtl) ────────────────────────────────────────
function parseOBJ(p) {
  const lines = fs.readFileSync(p, "utf-8").split("\n");
  const v = [], vt = [];
  const faces = []; // { v:[i,i,i], t:[i,i,i], mat: string|null }
  let currentMat = null;

  for (const lineRaw of lines) {
    const line = lineRaw.trim();
    if (!line || line.startsWith("#")) continue;
    const parts = line.split(/\s+/);
    const cmd = parts[0];

    if (cmd === "v") {
      v.push([parseFloat(parts[1]), parseFloat(parts[2]), parseFloat(parts[3])]);
    } else if (cmd === "vt") {
      vt.push([parseFloat(parts[1]), parseFloat(parts[2])]);
    } else if (cmd === "usemtl") {
      currentMat = parts[1];
    } else if (cmd === "f") {
      const verts = parts.slice(1).map(pp => {
        const [vi, ti] = pp.split("/");
        return { v: vi ? parseInt(vi, 10) - 1 : -1, t: ti ? parseInt(ti, 10) - 1 : -1 };
      });
      for (let i = 1; i < verts.length - 1; i++) {
        faces.push({ tri: [verts[0], verts[i], verts[i + 1]], mat: currentMat });
      }
    }
  }
  return { v, vt, faces };
}

const model = parseOBJ(objPath);
console.log(`📥 OBJ خوانده شد: vertices=${model.v.length} faces=${model.faces.length}`);

// ─── بارگذاری همه‌ی تکسچرهای لازم ────────────────────────────────────────────
const textureCache = new Map(); // fileName -> {width,height,data(Buffer RGBA)}

async function loadTexture(fileName) {
  if (textureCache.has(fileName)) return textureCache.get(fileName);
  const fullPath = path.join(dir, fileName);
  try {
    const { data, info } = await sharp(fullPath).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    let { width, height } = info;
    let pixels = data;

    // بعضی تکسچرها (مثل command_block_front.png یا observer_front.png) در واقع
    // یک نوار عمودیِ چند فریمِ انیمیشن‌ان (مثلاً 16x64 یعنی ۴ فریم پشت‌سرهم).
    // اگر همه‌ی نوار رو روی یک وجه بکشیم، الگو چندبار تکرار/فشرده به‌نظر می‌رسه.
    // پس فقط فریم اول (مربعِ بالای تصویر) رو نگه می‌داریم.
    if (height > width && width > 0 && height % width === 0) {
      const frameHeight = width;
      const frameBytes = width * frameHeight * 4;
      pixels = Buffer.from(data.subarray(0, frameBytes));
      height = frameHeight;
      console.log(`🎞  تکسچر انیمیت تشخیص داده شد: ${fileName} (${info.width}x${info.height}) → فریم اول (${width}x${height})`);
    }

    const tex = { width, height, data: pixels };
    textureCache.set(fileName, tex);
    return tex;
  } catch (e) {
    console.error(`⚠️ نتونستم تکسچر رو بخونم: ${fileName} (${e.message})`);
    const fallback = { width: 1, height: 1, data: Buffer.from([200, 40, 200, 255]) };
    textureCache.set(fileName, fallback);
    return fallback;
  }
}

const uniqueFiles = [...new Set(Object.values(matToTexFile))];
for (const f of uniqueFiles) await loadTexture(f);
console.log(`🖼  ${uniqueFiles.length} فایل تکسچر بارگذاری شد: ${uniqueFiles.join(", ")}`);

function textureForMat(matName) {
  const fileName = matToTexFile[matName];
  if (!fileName) return textureCache.get(uniqueFiles[0]) || { width: 1, height: 1, data: Buffer.from([200,200,200,255]) };
  return textureCache.get(fileName);
}

// ─── چرخش ایزومتریک (زاویه‌ی نمای اینونتوری ماینکرافت) ──────────────────────
// چرخش ~۴۵ درجه حول Y و سپس ~۳۰ درجه حول X (نمای کلاسیک ایزومتریک)
function rotateY(p, deg) {
  const a = (deg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  const [x, y, z] = p;
  return [c * x + s * z, y, -s * x + c * z];
}
function rotateX(p, deg) {
  const a = (deg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  const [x, y, z] = p;
  return [x, c * y - s * z, s * y + c * z];
}

const ROT_Y = -45;
const ROT_X = 35.264; // زاویه‌ی ایزومتریک واقعی (arctan(1/√2))

function transform(p) {
  return rotateX(rotateY(p, ROT_Y), ROT_X);
}

// ─── رندر ─────────────────────────────────────────────────────────────────
function render() {
  const transformed = model.v.map(transform);

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (const [x, y, z] of transformed) {
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
    if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
  }
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
  const extent = Math.max(maxX - minX, maxY - minY, 1e-6);
  const scale = (SIZE * 0.72) / extent;

  function project(p) {
    const x = (p[0] - cx) * scale;
    const y = (p[1] - cy) * scale;
    const z = p[2] - cz;
    return [SIZE / 2 + x, SIZE / 2 - y, z];
  }

  const fb = new Float64Array(SIZE * SIZE * 4);
  const zbuf = new Float64Array(SIZE * SIZE).fill(-Infinity);
  const hit = new Uint8Array(SIZE * SIZE);

  function sample(tex, u, v) {
    let uu = u - Math.floor(u), vv = v - Math.floor(v);
    if (!isFinite(uu)) uu = 0;
    if (!isFinite(vv)) vv = 0;
    const px = Math.min(tex.width - 1, Math.max(0, Math.floor(uu * tex.width)));
    const py = Math.min(tex.height - 1, Math.max(0, Math.floor((1 - vv) * tex.height)));
    const idx = (py * tex.width + px) * 4;
    return [tex.data[idx], tex.data[idx + 1], tex.data[idx + 2], tex.data[idx + 3]];
  }

  for (const { tri, mat } of model.faces) {
    const p0 = transformed[tri[0].v], p1 = transformed[tri[1].v], p2 = transformed[tri[2].v];
    if (!p0 || !p1 || !p2) continue;
    const tex = textureForMat(mat);

    const ax = p1[0]-p0[0], ay = p1[1]-p0[1], az = p1[2]-p0[2];
    const bx = p2[0]-p0[0], by = p2[1]-p0[1], bz = p2[2]-p0[2];
    let nx = ay*bz - az*by, ny = az*bx - ax*bz, nz = ax*by - ay*bx;
    const nlen = Math.hypot(nx, ny, nz) || 1;
    nx /= nlen; ny /= nlen; nz /= nlen;
    let brightness = 0.55 + 0.45 * Math.max(0, nz * 0.6 + ny * 0.4);
    brightness = Math.min(1, Math.max(0.35, brightness));

    const P0 = project(p0), P1 = project(p1), P2 = project(p2);
    const uvIdx = [tri[0].t, tri[1].t, tri[2].t];
    const hasUV = uvIdx.every(i => i >= 0 && model.vt[i]);
    const uv0 = hasUV ? model.vt[uvIdx[0]] : [0,0];
    const uv1 = hasUV ? model.vt[uvIdx[1]] : [0,0];
    const uv2 = hasUV ? model.vt[uvIdx[2]] : [0,0];

    const minSX = Math.max(0, Math.floor(Math.min(P0[0],P1[0],P2[0])));
    const maxSX = Math.min(SIZE-1, Math.ceil(Math.max(P0[0],P1[0],P2[0])));
    const minSY = Math.max(0, Math.floor(Math.min(P0[1],P1[1],P2[1])));
    const maxSY = Math.min(SIZE-1, Math.ceil(Math.max(P0[1],P1[1],P2[1])));
    if (minSX > maxSX || minSY > maxSY) continue;

    const area = (P1[0]-P0[0])*(P2[1]-P0[1]) - (P2[0]-P0[0])*(P1[1]-P0[1]);
    if (Math.abs(area) < 1e-9) continue;

    for (let py = minSY; py <= maxSY; py++) {
      for (let px = minSX; px <= maxSX; px++) {
        const sx = px+0.5, sy = py+0.5;
        const w0 = ((P1[0]-sx)*(P2[1]-sy) - (P2[0]-sx)*(P1[1]-sy)) / area;
        const w1 = ((P2[0]-sx)*(P0[1]-sy) - (P0[0]-sx)*(P2[1]-sy)) / area;
        const w2 = 1 - w0 - w1;
        if (w0 < -0.0001 || w1 < -0.0001 || w2 < -0.0001) continue;
        const depth = w0*P0[2] + w1*P1[2] + w2*P2[2];
        const zi = py*SIZE + px;
        if (depth <= zbuf[zi]) continue;

        let r=200,g=200,b=200,a=255;
        if (hasUV) {
          const u = w0*uv0[0] + w1*uv1[0] + w2*uv2[0];
          const vc = w0*uv0[1] + w1*uv1[1] + w2*uv2[1];
          [r,g,b,a] = sample(tex, u, vc);
          const tint = matTint[mat];
          if (tint) {
            // ضرب رنگ (multiply) دقیقاً همون کاری که ماینکرافت با تکسچرهای
            // خاکستریِ tintindex‌دار (چمن، برگ، آب و...) انجام می‌ده
            r *= tint[0]; g *= tint[1]; b *= tint[2];
          }
        }
        if (a < 10) continue;
        zbuf[zi] = depth;
        hit[zi] = 1;
        fb[zi*4]=r*brightness; fb[zi*4+1]=g*brightness; fb[zi*4+2]=b*brightness; fb[zi*4+3]=a;
      }
    }
  }

  // پس‌زمینه‌ی شفاف
  const out = Buffer.alloc(SIZE*SIZE*4);
  for (let i = 0; i < SIZE*SIZE; i++) {
    if (hit[i]) {
      out[i*4]=Math.round(fb[i*4]); out[i*4+1]=Math.round(fb[i*4+1]);
      out[i*4+2]=Math.round(fb[i*4+2]); out[i*4+3]=Math.round(fb[i*4+3]);
    }
  }
  return out;
}

const buf = render();
await sharp(buf, { raw: { width: SIZE, height: SIZE, channels: 4 } }).png().toFile(outPath);
console.log(`✅ آیکون ایزومتریک ساخته شد: ${outPath}`);
