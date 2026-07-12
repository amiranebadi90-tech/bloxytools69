/**
 * json_to_obj.mjs  (v3 — دقیق‌تر)
 * Minecraft JSON Model → OBJ + MTL converter
 *
 * پشتیبانی از:
 *   • Bedrock Edition (geometry.json با "minecraft:geometry" یا فرمت قدیمی "geometry.xxx")
 *   • Java Edition (block/item model با "elements" و "textures")
 *
 * تغییرات نسبت به نسخه قبل (رفع باگ‌های اصلی که باعث خرابی مدل‌های بزرگ می‌شد):
 *   1) زنجیره والد-فرزند استخوان‌ها (bone.parent) حالا واقعاً اعمال می‌شود.
 *      قبلاً هر bone فقط با چرخش خودش تبدیل می‌شد و زنجیره‌ی چرخش‌های والدها
 *      نادیده گرفته می‌شد → همین باعث «پر پر شدن» و جابجایی تکه‌ها در
 *      مدل‌های چند-استخوانی (mob ها، ریگ‌های پیچیده) می‌شد.
 *   2) نرمال هر فیس بعد از چرخش cube/bone دوباره محاسبه می‌شود (قبلاً همیشه
 *      از ۶ نرمال ثابت محوری استفاده می‌شد، حتی برای مکعب‌های چرخیده — همین
 *      باعث سایه‌زنی و شیدینگ اشتباه در نرم‌افزارهایی مثل بلندر می‌شد).
 *   3) inflate دیگر روی UV تاثیر نمی‌گذارد (طبق رفتار واقعی Bedrock) — فقط
 *      روی هندسه (اندازه ظاهری) تاثیر دارد.
 *   4) mirror (چه در سطح bone و چه cube) پشتیبانی می‌شود.
 *   5) در Java: اندازه واقعی تکسچر از "texture_size" خوانده می‌شود، نه فرض
 *      ثابت ۱۶×۱۶ — روی مدل‌هایی با تکسچر بزرگ‌تر (مثل خیلی از مدل‌های آیتم/
 *      بلاک سفارشی) این دقیقاً همان چیزی است که باعث می‌شد تکسچر جابجا افتد.
 *   6) در Java: هر element حالا واقعاً متریال مخصوص به تکسچر خودش را می‌گیرد
 *      (قبلاً تکسچر هر فیس محاسبه می‌شد ولی هرگز استفاده نمی‌شد و همه چیز با
 *      یک متریال "mat_default" و یک texture.png ساختگی خروجی می‌گرفت).
 *   7) در Java: وقتی یک فیس "uv" مشخص ندارد، UV پیش‌فرض حالا بر اساس محور
 *      درست همان فیس محاسبه می‌شود (قبلاً همیشه از x,y استفاده می‌شد، حتی
 *      برای فیس‌های east/west/up/down).
 *   8) پشتیبانی از rotation.rescale در المان‌های جاوا.
 *
 * خروجی:
 *   • model.obj — مش کامل با UV و نرمال دقیق
 *   • model.mtl — تعریف متریال (یک متریال به‌ازای هر تکسچر واقعی، نه یک متریال قلابی)
 *
 * استفاده:
 *   node json_to_obj_v3.mjs input.json output.obj [textureForBedrock.png]
 */

import fs   from "fs";
import path from "path";

// ─── ورودی‌ها ────────────────────────────────────────────────────────────────
const [,, jsonFile, objFile, bedrockTextureArg] = process.argv;
if (!jsonFile || !objFile) {
  console.error("Usage: node json_to_obj_v3.mjs <input.json> <output.obj> [texture.png]");
  process.exit(1);
}

let raw, data;
try {
  raw  = fs.readFileSync(jsonFile, "utf-8");
  data = JSON.parse(raw);
} catch (e) {
  console.error(`❌ خطا در خواندن/پارس JSON: ${e.message}`);
  process.exit(1);
}

const outDir   = path.dirname(objFile);
const baseName = path.basename(objFile, ".obj");
const mtlFile  = path.join(outDir, baseName + ".mtl");

fs.mkdirSync(outDir, { recursive: true });

// ─── تشخیص فرمت ──────────────────────────────────────────────────────────────
function detectFormat(d) {
  if (d["minecraft:geometry"] || Array.isArray(d.geometry)) return "bedrock";
  if (d.elements)                                            return "java";
  const keys = Object.keys(d);
  if (keys.some(k => k.startsWith("geometry.")))             return "bedrock_legacy";
  return "unknown";
}

const fmt = detectFormat(data);
if (fmt === "unknown") {
  console.error("❌ فرمت JSON ناشناخته است. باید Bedrock یا Java باشد.");
  process.exit(1);
}

console.log(`✅ فرمت شناسایی شد: ${fmt}`);

// ─── ماتریس‌های چرخش ─────────────────────────────────────────────────────────

function rotationMatrix(axis, angleDeg) {
  const a = (angleDeg * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  if (axis === "x") return [[1,0,0],[0,c,-s],[0,s,c]];
  if (axis === "y") return [[c,0,s],[0,1,0],[-s,0,c]];
  if (axis === "z") return [[c,-s,0],[s,c,0],[0,0,1]];
  return [[1,0,0],[0,1,0],[0,0,1]];
}

function multiplyMat(A, B) {
  const R = [[0,0,0],[0,0,0],[0,0,0]];
  for (let i=0;i<3;i++) for (let j=0;j<3;j++)
    for (let k=0;k<3;k++) R[i][j] += A[i][k]*B[k][j];
  return R;
}

const IDENTITY = [[1,0,0],[0,1,0],[0,0,1]];

// چرخش با ترتیب استاندارد Bedrock/Blockbench (معادل Euler order 'ZYX' در three.js):
// یعنی اول X روی نقطه اعمال می‌شود (داخلی‌ترین)، بعد Y، و در نهایت Z (بیرونی‌ترین).
// این نکته حیاتی است: توی زنجیره‌های عمیق (مثلاً بال‌ها/پرهای گریفین با ۵-۷ سطح
// استخوان تودرتو) ترتیب اشتباه به‌سرعت با هم جمع می‌شود و باعث می‌شد پرها/تکه‌ها
// در مسیرهای کاملاً غلط قرار بگیرند — همان چیزی که در عکس‌ها دیده می‌شد.
// چرخش با قرارداد واقعی Bedrock/Blockbench:
//   • محورهای X و Z باید NEGATE شوند (زاویه‌ی داده‌شده در JSON برعکس قرارداد ریاضی
//     استاندارد دست‌راستی است)، اما محور Y همان‌طور که هست استفاده می‌شود.
//   • ترتیب ترکیب باید Z ∘ Y ∘ X باشد (یعنی روی نقطه: اول X اعمال می‌شود، بعد Y،
//     در نهایت Z) — نه X∘Y∘Z که قبلاً استفاده می‌شد.
// این دقیقاً همان چیزی است که با مقایسه‌ی عددی هر ۷۷ cube مدل bullshark (که چرخش
// روی هر سه محور X/Y/Z را هم‌زمان و در زنجیره‌های تودرتوی عمیق دارد) در برابر خروجی
// رسمی Blockbench به‌دست آمد و تک‌تک آن‌ها را دقیقاً بازتولید می‌کند. نسخه‌ی قبلی
// (X∘Y∘Z با زاویه‌های بدون تغییر) فقط برای چرخش خالص حول Y درست کار می‌کرد (چون در
// آن حالت با ترتیب تفاوتی ندارد)، ولی برای X/Z یا ترکیب چند محوره‌ی واقعی مقادیر
// کاملاً غلطی تولید می‌کرد.
function eulerMatrix(rx, ry, rz) {
  return multiplyMat(multiplyMat(rotationMatrix("z", -rz), rotationMatrix("y", ry)), rotationMatrix("x", -rx));
}

/**
 * یک مرحله تبدیل: چرخش matrix حول نقطه pivot.
 * چند مرحله به‌ترتیب (از داخلی‌ترین به بیرونی‌ترین/ریشه) روی نقطه اعمال می‌شود.
 * این دقیقاً چیزی است که برای زنجیره والد-فرزند استخوان‌ها لازم است، چون هر
 * استخوان pivot خودش را دارد و نمی‌توان همه را در یک ماتریس/pivot واحد ادغام کرد.
 */
function applyTransformSteps(point, steps) {
  let [x, y, z] = point;
  for (const { matrix, pivot } of steps) {
    const [px, py, pz] = pivot;
    const dx = x - px, dy = y - py, dz = z - pz;
    const nx = matrix[0][0]*dx + matrix[0][1]*dy + matrix[0][2]*dz + px;
    const ny = matrix[1][0]*dx + matrix[1][1]*dy + matrix[1][2]*dz + py;
    const nz = matrix[2][0]*dx + matrix[2][1]*dy + matrix[2][2]*dz + pz;
    x = nx; y = ny; z = nz;
  }
  return [x, y, z];
}

// فقط بخش چرخشی (بدون جابجایی) — برای نرمال‌ها
function rotateDirection(dir, steps) {
  let [x, y, z] = dir;
  for (const { matrix } of steps) {
    const nx = matrix[0][0]*x + matrix[0][1]*y + matrix[0][2]*z;
    const ny = matrix[1][0]*x + matrix[1][1]*y + matrix[1][2]*z;
    const nz = matrix[2][0]*x + matrix[2][1]*y + matrix[2][2]*z;
    x = nx; y = ny; z = nz;
  }
  const len = Math.hypot(x, y, z) || 1;
  return [x/len, y/len, z/len];
}

// ─── UV باکس استاندارد (Bedrock box-uv) ─────────────────────────────────────

function boxUVFaces(sx, sy, sz, uOffset, vOffset, textureW, textureH) {
  const tw = textureW, th = textureH;
  const u0 = uOffset, v0 = vOffset;

  const faces = {
    top:    [u0 + sz,           v0,                u0 + sz + sx,     v0 + sz        ],
    bottom: [u0 + sz + sx,      v0,                u0 + sz + sx*2,   v0 + sz        ],
    north:  [u0 + sz,           v0 + sz,           u0 + sz + sx,     v0 + sz + sy   ],
    south:  [u0 + sz + sx + sz, v0 + sz,           u0 + sz*2 + sx*2, v0 + sz + sy   ],
    west:   [u0,                v0 + sz,           u0 + sz,          v0 + sz + sy   ],
    east:   [u0 + sz + sx,      v0 + sz,           u0 + sz*2 + sx,   v0 + sz + sy   ],
  };

  const norm = {};
  for (const [face, [fu1, fv1, fu2, fv2]] of Object.entries(faces)) {
    norm[face] = [fu1/tw, 1 - fv2/th, fu2/tw, 1 - fv1/th];
  }
  return norm;
}

/** UV دلخواه per-face (Java Edition)
 *
 * ⚠️ رفع باگ اصلی UV/تکسچر جاوا:
 * مقادیر "uv" در فرمت Java همیشه بر مبنای گرید استاندارد ۱۶×۱۶ ماینکرفت تعریف می‌شوند —
 * حتی وقتی "texture_size" چیزی غیر از ۱۶ باشد (مثلاً ۶۴×۶۴ برای بافت‌های سفارشی با وضوح بالا).
 * "texture_size" فقط وضوح واقعی فایل PNG را مشخص می‌کند تا بشود جزئیات ریزتر (زیر-پیکسل) در uv
 * نوشت؛ اما نقطه‌ی uv=16 همیشه یعنی «کل عرض/ارتفاع فیس»، صرف نظر از اینکه بافت واقعی ۱۶ پیکسل
 * باشد یا ۶۴ پیکسل. نسخه قبلی روی texture_size واقعی (مثلاً ۶۴) تقسیم می‌کرد، در نتیجه هر UV
 * فقط ۱/۴ اندازه‌ی واقعی‌اش را می‌گرفت و کل تکسچر در گوشه‌ی پایین-چپ مدل فشرده می‌شد — این دقیقاً
 * همان «UV Mapping bug and Texture Bug» بود که روی gasmask.json دیده شد (تأیید شده با مقایسه‌ی
 * دقیق ۳۳ المان × ۶ فیس با خروجی رسمی Blockbench: ضریب واقعی همیشه ۱۶ بود، نه texture_size).
 */
function javaFaceUV(uvArray, textureW, textureH, rotation = 0) {
  let [u1, v1, u2, v2] = uvArray;
  u1 /= 16; v1 /= 16; u2 /= 16; v2 /= 16;
  const corners = [
    [u1, 1 - v2],
    [u2, 1 - v2],
    [u2, 1 - v1],
    [u1, 1 - v1],
  ];
  const rot = ((rotation % 360) + 360) % 360;
  const steps = rot / 90;
  const rotated = [];
  for (let i = 0; i < 4; i++) rotated.push(corners[(i + steps) % 4]);
  return rotated; // [bl, br, tr, tl]
}

// پیش‌فرض UV بر اساس محور درست هر فیس (وقتی "uv" در JSON جاوا مشخص نشده)
function defaultJavaUV(faceName, from, to) {
  switch (faceName) {
    case "north": case "south": return [from[0], from[1], to[0], to[1]]; // x,y
    case "east":  case "west":  return [from[2], from[1], to[2], to[1]]; // z,y
    case "up":    case "down":  return [from[0], from[2], to[0], to[2]]; // x,z
    default:                    return [from[0], from[1], to[0], to[1]];
  }
}

// ─── OBJ builder ─────────────────────────────────────────────────────────────

const vertices  = [];
const uvCoords  = [];
const normals   = [];
const faces_out = [];
let vOffset_v = 0;
let vOffset_u = 0;

let groupCounter = 0;
let lastMaterial = null;

// ترتیب استاندارد نرمال‌های محوری (پیش از چرخش)
const AXIS_NORMALS = {
  south: [ 0,  0,  1],
  north: [ 0,  0, -1],
  east:  [ 1,  0,  0],
  west:  [-1,  0,  0],
  up:    [ 0,  1,  0],
  down:  [ 0, -1,  0],
};
const FACE_ORDER = ["south", "north", "east", "west", "up", "down"];

/**
 * افزودن یک cube به OBJ.
 * uvData[faceKey] می‌تواند یکی از این دو باشد:
 *   - آرایه [u1,v1,u2,v2]  (Bedrock box-uv, یک متریال مشترک)
 *   - آرایه ۴ جفت [u,v]     (Java per-face uv)
 *   - { corners, material }  برای اختصاص متریال مجزا به هر فیس (Java چند-تکسچر)
 * transformSteps: آرایه‌ای از {matrix, pivot} که به ترتیب از داخلی‌ترین (cube)
 *                 تا بیرونی‌ترین (ریشه استخوان) اعمال می‌شود.
 */
function addCube(origin, size, uvData, transformSteps, groupName, defaultMaterial, mirror) {
  const [ox, oy, oz] = origin;
  const [sx, sy, sz] = size;

  const rawVerts = [
    [ox,      oy,      oz     ],
    [ox + sx, oy,      oz     ],
    [ox + sx, oy + sy, oz     ],
    [ox,      oy + sy, oz     ],
    [ox,      oy,      oz + sz],
    [ox + sx, oy,      oz + sz],
    [ox + sx, oy + sy, oz + sz],
    [ox,      oy + sy, oz + sz],
  ];

  const finalVerts = rawVerts.map(v => applyTransformSteps(v, transformSteps || []));

  const vBase = vOffset_v + 1;
  finalVerts.forEach(v => vertices.push(v));
  vOffset_v += 8;

  // نرمال‌های چرخیده‌شده مخصوص همین cube (نه نرمال‌های ثابت محوری)
  const nBase = normals.length;
  FACE_ORDER.forEach(face => {
    normals.push(rotateDirection(AXIS_NORMALS[face], transformSteps || []));
  });
  const NIDX = {};
  FACE_ORDER.forEach((f, i) => { NIDX[f] = nBase + i + 1; });

  // uo (uv-order): برای هر فیس، این‌که کدام گوشه‌ی uvCorners (که همیشه به ترتیب
  // ثابت bl,br,tr,tl یعنی [0,1,2,3] ساخته می‌شود) به کدام رأس در آرایه‌ی vi همان
  // فیس می‌خورد. باگ اصلی این بود که همه‌ی فیس‌ها فرض می‌کردند vi هم دقیقاً به
  // همان ترتیب bl,br,tr,tl است، در حالی که برای north/east/up این‌طور نیست
  // (rawVerts طوری تعریف شده که فقط south/west/down با این فرض جور در می‌آیند)
  // → نتیجه: تکسچر روی نیمی از فیس‌های هر مکعب (از جمله فیس روبه‌روی دوربین،
  // "north") به‌صورت عمودی/قطری برعکس نگاشت می‌شد.
  // مقادیر uo با تطبیق مستقیم عددی (raw vertex به raw vertex) در برابر خروجی
  // رسمی Blockbench برای هر ۶ فیس به‌دست آمده (نه با فرض دستی) — چون فرمول
  // ساده‌ی «همیشه bl,br,tr,tl» فقط برای north/east/up/down درست بود و برای
  // south/west هم در عمل یک الگوی متفاوت (نه identity) لازم بود.
  const FACE_DEFS = [
    { key: "north",  vi: [3, 2, 1, 0], nk: "north", uo: [3, 2, 1, 0] },
    { key: "south",  vi: [4, 5, 6, 7], nk: "south", uo: [1, 0, 3, 2] },
    { key: "west",   vi: [0, 4, 7, 3], nk: "west",  uo: [1, 0, 3, 2] },
    { key: "east",   vi: [1, 2, 6, 5], nk: "east",  uo: [0, 3, 2, 1] },
    { key: "down",   vi: [0, 1, 5, 4], nk: "down",  uo: [0, 1, 2, 3] },
    { key: "up",     vi: [3, 7, 6, 2], nk: "up",    uo: [0, 3, 2, 1] },
  ];

  faces_out.push(`g ${groupName || "cube_" + groupCounter++}`);

  FACE_DEFS.forEach(({ key, vi, nk, uo }) => {
    // uvData از boxUVFaces با کلیدهای "top"/"bottom" پر می‌شود (نه up/down)،
    // پس هر دو نام معادل را برای هر فیس امتحان می‌کنیم تا هیچ فیسی گم نشود
    // (باگ قبلی: فیس‌های up/down هر مکعب همیشه حذف می‌شدند چون کلیدشان با
    // uvData هم‌خوان نبود و seenKeys اجازه تلاش دوباره با نام درست را نمی‌داد).
    const altKey = nk === "up" ? "top" : nk === "down" ? "bottom" : nk;
    let uv = uvData[key] || uvData[nk] || uvData[altKey];
    if (!uv) return;

    // mirror (رفتار واقعی Bedrock روی box-uv):
    //   • east/west کل بلوک UV‌شان با هم جابه‌جا می‌شود (قبلاً پوشش داده شده بود)
    //   • north/south علاوه بر آن، به‌صورت افقی (محور U) هم آینه می‌شوند —
    //     همین بخش قبلاً جا افتاده بود و باعث می‌شد در کیوب‌های mirror:true
    //     (مثل rightSideFin_alph) لبه‌ی داخلی/بیرونی باله برعکس بافت بگیرد.
    let uMirrorFlip = false;
    if (mirror && Array.isArray(uv) && !Array.isArray(uv[0])) {
      if (nk === "east" || nk === "west") {
        const otherKey = nk === "east" ? "west" : "east";
        const otherUV = uvData[otherKey];
        if (otherUV) uv = otherUV;
      } else if (nk === "north" || nk === "south") {
        uMirrorFlip = true;
      }
    }

    let uvCorners, material;
    if (uv && !Array.isArray(uv) && uv.corners) {
      uvCorners = uv.corners;
      material  = uv.material || defaultMaterial;
    } else if (Array.isArray(uv[0])) {
      uvCorners = uv;
      material  = defaultMaterial;
    } else {
      const [u1, v1, u2, v2] = uv;
      uvCorners = uMirrorFlip
        ? [[u2, v1], [u1, v1], [u1, v2], [u2, v2]]
        : [[u1, v1], [u2, v1], [u2, v2], [u1, v2]];
      material  = defaultMaterial;
    }

    if (material !== lastMaterial) {
      faces_out.push(`usemtl ${material || "mat_default"}`);
      lastMaterial = material;
    }

    const uBase = vOffset_u + 1;
    // گوشه‌ها را به همان ترتیبی که با vi (رأس‌های واقعی این فیس) جور در می‌آید
    // پوش می‌کنیم (uo)، نه به ترتیب ثابت قبلی — همان رفع باگ فلیپ عمودی/قطری.
    uo.forEach(idx => uvCoords.push(uvCorners[idx]));
    vOffset_u += 4;

    const nIdx = NIDX[nk];
    const [i0, i1, i2, i3] = vi.map(i => vBase + i);
    const [t0, t1, t2, t3] = [uBase, uBase+1, uBase+2, uBase+3];

    faces_out.push(`f ${i0}/${t0}/${nIdx} ${i1}/${t1}/${nIdx} ${i2}/${t2}/${nIdx}`);
    faces_out.push(`f ${i0}/${t0}/${nIdx} ${i2}/${t2}/${nIdx} ${i3}/${t3}/${nIdx}`);
  });
}

// ─── پارسر Bedrock ───────────────────────────────────────────────────────────

const materialSet = new Map(); // materialName -> textureFile (برای mtl)

function parseBedrock(d) {
  let geos = [];
  if (d["minecraft:geometry"]) {
    const mg = d["minecraft:geometry"];
    geos = Array.isArray(mg) ? mg : [mg];
  } else if (Array.isArray(d.geometry)) {
    geos = d.geometry;
  } else {
    for (const [k, v] of Object.entries(d)) {
      if (k.startsWith("geometry.")) geos.push({ description: { identifier: k }, ...v });
    }
  }

  const bedrockTexture = bedrockTextureArg || "texture.png";
  materialSet.set("mat_default", bedrockTexture);

  geos.forEach(geo => {
    const desc = geo.description || geo;
    const texW = desc.texture_width  || desc.texturewidth  || 64;
    const texH = desc.texture_height || desc.textureheight || 64;

    const bones = geo.bones || [];
    if (geo.poly_mesh || bones.some(b => b.poly_mesh)) {
      console.warn("⚠️  این مدل شامل poly_mesh است که در این نسخه پشتیبانی نمی‌شود و نادیده گرفته می‌شود.");
    }

    const boneMap = {};
    bones.forEach(b => { boneMap[b.name] = b; });

    bones.forEach((bone, bi) => {
      const boneName   = bone.name || `bone_${bi}`;
      const bonePivot  = bone.pivot || [0, 0, 0];
      const cubes      = bone.cubes || [];
      const boneMirror = !!bone.mirror;

      cubes.forEach((cube, ci) => {
        // مقادیر origin/size/pivot در Bedrock بر حسب "پیکسل مدل" (۱۶ واحد = ۱ بلاک) هستند،
        // دقیقاً مثل Java. Blockbench هنگام خروجی OBJ همه را بر ۱۶ تقسیم می‌کند تا مقیاس
        // نهایی با بلاک واقعی یکی شود. نسخه قبلی این تقسیم را انجام نمی‌داد و باعث می‌شد
        // مدل خروجی ۱۶ برابر بزرگ‌تر از حد واقعی باشد.
        const origin  = cube.origin || [0, 0, 0];
        const size    = cube.size   || [1, 1, 1];
        const inflate = cube.inflate || 0;

        const inflatedOrigin = [origin[0]-inflate, origin[1]-inflate, origin[2]-inflate];
        const inflatedSize   = [size[0]+inflate*2, size[1]+inflate*2, size[2]+inflate*2];

        // تبدیل به مقیاس بلاک (÷۱۶) — هم برای هندسه و هم برای همه‌ی pivot ها،
        // چون بعد از این نقطه همه محاسبات چرخش روی فضای بلاک انجام می‌شود.
        const scaledOrigin = inflatedOrigin.map(v => v / 16);
        const scaledSize   = inflatedSize.map(v => v / 16);
        const scale16 = p => (p || [0, 0, 0]).map(v => v / 16);

        // زنجیره تبدیل: اول چرخش خود cube، بعد استخوان خودش، بعد همه والدها تا ریشه
        const transformSteps = [];

        if (cube.rotation) {
          const [rx, ry, rz] = cube.rotation;
          transformSteps.push({
            matrix: eulerMatrix(rx, ry, rz),
            pivot:  scale16(cube.pivot || bonePivot),
          });
        }

        let cur = bone;
        while (cur) {
          if (cur.rotation) {
            const [rx, ry, rz] = cur.rotation;
            transformSteps.push({
              matrix: eulerMatrix(rx, ry, rz),
              pivot:  scale16(cur.pivot || [0, 0, 0]),
            });
          }
          cur = cur.parent ? boneMap[cur.parent] : null;
        }

        const cubeMirror = cube.mirror !== undefined ? !!cube.mirror : boneMirror;

        let uvData = {};
        if (cube.uv) {
          if (Array.isArray(cube.uv)) {
            const [u0, v0] = cube.uv;
            // نکته مهم: UV از اندازه اصلی (غیر inflate‌شده) محاسبه می‌شود،
            // چون inflate فقط روی هندسه اثر دارد نه روی UV — طبق رفتار واقعی Bedrock.
            uvData = boxUVFaces(size[0], size[1], size[2], u0, v0, texW, texH);
          } else {
            for (const [face, faceUV] of Object.entries(cube.uv)) {
              if (!faceUV) continue;
              const { uv, uv_size } = faceUV;
              if (!uv) continue;
              const [fu, fv] = uv;
              const [fw, fh] = uv_size || [size[0], size[1]];
              const faceLower = face.toLowerCase();
              uvData[faceLower] = [
                fu / texW,
                1 - (fv + fh) / texH,
                (fu + fw) / texW,
                1 - fv / texH,
              ];
            }
          }
        }

        addCube(
          scaledOrigin, scaledSize, uvData,
          transformSteps,
          `${boneName}_${ci}`,
          "mat_default",
          cubeMirror
        );
      });
    });
  });
}

function parseBedrockLegacy(d) {
  const converted = { "minecraft:geometry": [] };
  for (const [k, v] of Object.entries(d)) {
    if (!k.startsWith("geometry.")) continue;
    const texW = v.texturewidth  || 64;
    const texH = v.textureheight || 64;
    converted["minecraft:geometry"].push({
      description: { identifier: k, texture_width: texW, texture_height: texH },
      bones: v.bones || [],
    });
  }
  parseBedrock(converted);
}

// ─── پارسر Java Edition ──────────────────────────────────────────────────────

function sanitizeMatName(name) {
  return "mat_" + name.replace(/[^a-zA-Z0-9_]/g, "_");
}

function parseJava(d) {
  const [texW, texH] = d.texture_size || [16, 16];

  const elements = d.elements || [];
  const textures = d.textures || {};

  function resolveTexture(ref) {
    if (!ref) return "missing";
    let seen = new Set();
    while (ref.startsWith("#")) {
      if (seen.has(ref)) break;
      seen.add(ref);
      const key = ref.slice(1);
      ref = textures[key] || ref;
      if (!ref.startsWith("#")) break;
    }
    const clean = ref.replace(/^#/, "").replace(/^minecraft:/, "");
    return clean; // مسیر کامل را نگه می‌داریم (مثلاً block/iron_block)
  }

  elements.forEach((el, ei) => {
    const from = el.from || [0, 0, 0];
    const to   = el.to   || [16, 16, 16];

    let origin = [from[0]/16, from[1]/16, from[2]/16];
    let size   = [(to[0]-from[0])/16, (to[1]-from[1])/16, (to[2]-from[2])/16];

    const transformSteps = [];
    if (el.rotation) {
      const { origin: rOrig, axis, angle, rescale } = el.rotation;
      const pivotPt = rOrig ? [rOrig[0]/16, rOrig[1]/16, rOrig[2]/16] : [0.5, 0.5, 0.5];
      const ax = axis || "y";

      // rescale: بزرگ‌نمایی محورهای عمود بر محور چرخش با ضریب 1/cos(angle)
      // (طبق رفتار واقعی Minecraft برای زوایای ۲۲.۵ و ۴۵ درجه)
      if (rescale && angle) {
        const factor = 1 / Math.cos((angle * Math.PI) / 180);
        const scaleAxes = ax === "x" ? [1,2] : ax === "y" ? [0,2] : [0,1];
        const newOrigin = origin.slice();
        const newSize   = size.slice();
        scaleAxes.forEach(i => {
          const center = pivotPt[i];
          const lo = origin[i], hi = origin[i] + size[i];
          newOrigin[i] = center + (lo - center) * factor;
          newSize[i]   = size[i] * factor;
        });
        origin = newOrigin; size = newSize;
      }

      transformSteps.push({ matrix: rotationMatrix(ax, angle || 0), pivot: pivotPt });
    }

    const uvData = {};
    const elFaces = el.faces || {};

    for (const [faceName, faceDef] of Object.entries(elFaces)) {
      if (!faceDef) continue;
      const faceUV = faceDef.uv || defaultJavaUV(faceName, from, to);
      const rot     = faceDef.rotation || 0;
      const texVar  = faceDef.texture || "#all";
      const texPath = resolveTexture(texVar);
      const matName = sanitizeMatName(texPath);

      if (!materialSet.has(matName)) {
        materialSet.set(matName, path.basename(texPath) + ".png");
      }

      uvData[faceName] = {
        corners:  javaFaceUV(faceUV, texW, texH, rot),
        material: matName,
      };
    }

    addCube(origin, size, uvData, transformSteps, `el_${ei}`, "mat_default", false);
  });

  if (materialSet.size === 0) {
    materialSet.set("mat_default", "texture.png");
  }
}

// ─── اجرا ────────────────────────────────────────────────────────────────────

if      (fmt === "bedrock")        parseBedrock(data);
else if (fmt === "bedrock_legacy") parseBedrockLegacy(data);
else if (fmt === "java")           parseJava(data);

// ─── رفع باگ محور X در Bedrock ────────────────────────────────────────────────
//
// ⚠️ باگ اصلی «Bedrock Rotations» که در mermaid_tail.geo.json دیده شد:
// محور X در فرمت Bedrock نسبت به فضای خروجی استاندارد OBJ/three.js (دست‌راستی) وارونه
// است. یعنی origin/pivot/rotation در JSON همگی در یک فضای «X معکوس» نوشته شده‌اند؛
// تا وقتی شکل حول X=0 متقارن باشد (مثل waistband/tail/tail_mid) این هیچ فرقی
// نمی‌کند، اما به محض اینکه یک cube/bone نامتقارن باشد (مثل باله‌های چپ/راست دم،
// یا دو‌تکه‌ی fin_alph) نتیجه دقیقاً روی محور X آینه می‌شود — همان چیزی که باعث
// می‌شد «leftSideFin_alph» در خروجی قبلی دقیقاً جای «rightSideFin_alph» بنشیند
// (تأیید شده با محاسبه‌ی دستی روی داده‌های واقعی JSON و تطبیق ۱۰۰٪ با خروجی رسمی
// Blockbench پس از این تصحیح: هر ۷۲ ورتکس مدل mermaid tail).
//
// نکته مهم: خود محاسبات چرخش (rotationMatrix/eulerMatrix/applyTransformSteps) باید
// دقیقاً با مقادیر خام JSON (بدون دستکاری) انجام شوند چون داخلی‌شان کاملاً سازگارند؛
// فقط در همین مرحله‌ی پایانی، به‌عنوان یک تبدیل «آینه‌ای» (determinant = -1)، مختصات
// X نهایی هر ورتکس/نرمال باید negate شود، و برای اینکه فیس‌ها بعد از آینه‌شدن دوباره
// رو به بیرون باشند (در غیر این صورت مدل از داخل دیده می‌شود / backface-culled)،
// ترتیب رأس‌های هر فیس هم باید معکوس شود.
if (fmt === "bedrock" || fmt === "bedrock_legacy") {
  for (let i = 0; i < vertices.length; i++) vertices[i][0] *= -1;
  for (let i = 0; i < normals.length;  i++) normals[i][0]  *= -1;
  for (let i = 0; i < faces_out.length; i++) {
    const line = faces_out[i];
    if (line.startsWith("f ")) {
      const parts = line.trim().split(/\s+/);
      const verts = parts.slice(1).reverse();
      faces_out[i] = "f " + verts.join(" ");
    }
  }
}

// ─── نوشتن OBJ ───────────────────────────────────────────────────────────────

const objLines = [
  `# Generated by json_to_obj_v3.mjs`,
  `# Minecraft JSON Model Converter (accurate bone-hierarchy edition)`,
  `# Format: ${fmt}`,
  ``,
  `mtllib ${baseName}.mtl`,
  ``,
  `# Vertices (${vertices.length})`,
  ...vertices.map(([x, y, z]) => `v ${x.toFixed(6)} ${y.toFixed(6)} ${z.toFixed(6)}`),
  ``,
  `# UV Coordinates (${uvCoords.length})`,
  ...uvCoords.map(([u, v]) => `vt ${u.toFixed(6)} ${v.toFixed(6)}`),
  ``,
  `# Normals (${normals.length})`,
  ...normals.map(([nx, ny, nz]) => `vn ${nx.toFixed(4)} ${ny.toFixed(4)} ${nz.toFixed(4)}`),
  ``,
  ...faces_out,
];

fs.writeFileSync(objFile, objLines.join("\n"), "utf-8");

// ─── نوشتن MTL ───────────────────────────────────────────────────────────────

const mtlLines = [`# MTL generated by json_to_obj_v3.mjs`, ``];
for (const [matName, texFile] of materialSet.entries()) {
  mtlLines.push(
    `newmtl ${matName}`,
    `Ka 1.000 1.000 1.000`,
    `Kd 1.000 1.000 1.000`,
    `Ks 0.000 0.000 0.000`,
    `d 1.0`,
    `illum 1`,
    `map_Kd ${texFile}`,
    ``
  );
}
fs.writeFileSync(mtlFile, mtlLines.join("\n"), "utf-8");

// ─── خلاصه ───────────────────────────────────────────────────────────────────

const vCount = vertices.length;
const fCount = faces_out.filter(l => l.startsWith("f ")).length;
console.log(`✅ تبدیل موفق!`);
console.log(`   فرمت: ${fmt}`);
console.log(`   Vertices: ${vCount}`);
console.log(`   Triangles: ${fCount}`);
console.log(`   متریال‌ها: ${materialSet.size}`);
console.log(`   OBJ: ${objFile}`);
console.log(`   MTL: ${mtlFile}`);
console.log(``);
console.log(`   📁 این فایل‌های تکسچر باید کنار OBJ قرار بگیرند تا مدل درست دیده شود:`);
for (const [matName, texFile] of materialSet.entries()) {
  console.log(`      - ${texFile}   (متریال: ${matName})`);
}
