import fs from "fs";
import path from "path";
import sharp from "sharp";
import archiver from "archiver";

const input = process.argv[2];
const output = process.argv[3];

if (!input || !output) {
  console.log("Usage: node item3d.mjs input.png output.obj");
  process.exit(1);
}

const SCALE = 1 / 16;
const DEPTH = 0.07;
const ALPHA_THRESHOLD = 40;

const { data, info } = await sharp(input)
  .ensureAlpha()
  .raw()
  .toBuffer({ resolveWithObject: true });

const WIDTH = info.width;
const HEIGHT = info.height;

function isSolid(x, y) {
  if (x < 0 || y < 0 || x >= WIDTH || y >= HEIGHT)
    return false;

  const i = (y * WIDTH + x) * 4;
  return data[i + 3] > ALPHA_THRESHOLD;
}

// ---------------- OBJ BUFFERS ----------------

const vertices = [];
const uvs = [];
const faces = [];

let vertexIndex = 1;
let uvIndex = 1;

// ---------------- HELPERS ----------------

const vertexCache = new Map();

function addVertex(x, y, z) {

  const key = `${x},${y},${z}`;

  if (vertexCache.has(key)) {
    return vertexCache.get(key);
  }

  const id = vertexIndex++;

  vertices.push(`v ${x} ${y} ${z}`);

  vertexCache.set(key, id);

  return id;
}

function addUV(u, v) {
  uvs.push(`vt ${u} ${v}`);
  return uvIndex++;
}

function addTri(v1, t1, v2, t2, v3, t3) {
  faces.push(
    `f ${v1}/${t1} ${v2}/${t2} ${v3}/${t3}`
  );
}

function addQuad(v1,v2,v3,v4,t1,t2,t3,t4) {

  addTri(v1,t1,v3,t3,v2,t2);
  addTri(v1,t1,v4,t4,v3,t3);

}

// ---------------- FACE BUILDER ----------------

function buildVoxel(x, y) {

const px = (x - WIDTH / 2) * SCALE;
const py = ((HEIGHT - y - 1) - HEIGHT / 2) * SCALE;

  const z0 = -DEPTH / 2;
  const z1 = DEPTH / 2;

  const v000 = addVertex(px,     py,     z0);
  const v100 = addVertex(px + SCALE, py, z0);
  const v110 = addVertex(px + SCALE, py + SCALE, z0);
  const v010 = addVertex(px, py + SCALE, z0);

  const v001 = addVertex(px,         py,         z1);
  const v101 = addVertex(px + SCALE, py,         z1);
  const v111 = addVertex(px + SCALE, py + SCALE, z1);
  const v011 = addVertex(px,         py + SCALE, z1);

  const u1 = x / WIDTH;
  const u2 = (x + 1) / WIDTH;

  const vv1 = (HEIGHT - y - 1) / HEIGHT;
  const vv2 = (HEIGHT - y) / HEIGHT;

  const t1 = addUV(u1, vv1);
  const t2 = addUV(u2, vv1);
  const t3 = addUV(u2, vv2);
  const t4 = addUV(u1, vv2);
  const pixelUV = addUV(
  (x + 0.5) / WIDTH,
  (HEIGHT - y - 0.5) / HEIGHT
);

// FRONT
addQuad(
  v001,v101,v111,v011,
  t1,t2,t3,t4
);

// BACK
addQuad(
  v100,v000,v010,v110,
  t1,t2,t3,t4
);
  
// LEFT

if (!isSolid(x - 1, y)) {

addQuad(
  v010,v011,v001,v000,
    pixelUV,pixelUV,pixelUV,pixelUV
);
}

  // RIGHT

if (!isSolid(x + 1, y)) {

addQuad(
  v111,v110,v100,v101,
  pixelUV,pixelUV,pixelUV,pixelUV
);
}

// TOP

if (!isSolid(x, y - 1)) {

addQuad(
  v110,v111,v011,v010,
  pixelUV,pixelUV,pixelUV,pixelUV
);
}

// BOTTOM

if (!isSolid(x, y + 1)) {

  addQuad(
    v000,v100,v101,v001,
    pixelUV,pixelUV,pixelUV,pixelUV
  );
}
}

// ---------------- BUILD ----------------

for (let y = 0; y < HEIGHT; y++) {

  for (let x = 0; x < WIDTH; x++) {

    if (!isSolid(x, y))
      continue;

    buildVoxel(x, y);
  }
}

const baseName = path.basename(output, ".obj");

const objContent =
`mtllib ${baseName}.mtl
usemtl Material

${vertices.join("\n")}

${uvs.join("\n")}

${faces.join("\n")}
`;

fs.writeFileSync(output, objContent);

fs.writeFileSync(
  output.replace(".obj", ".mtl"),
`newmtl Material
Ka 1 1 1
Kd 1 1 1
Ks 0 0 0
d 1
illum 2
map_Kd ${baseName}.png
`
);

fs.copyFileSync(
  input,
  output.replace(".obj", ".png")
);

const archive = archiver(
  "zip",
  { zlib: { level: 9 } }
);

const stream = fs.createWriteStream(
  output.replace(".obj", ".zip")
);

archive.pipe(stream);

archive.file(output, {
  name: `${baseName}.obj`
});

archive.file(
  output.replace(".obj",".mtl"),
  {
    name: `${baseName}.mtl`
  }
);

archive.file(
  output.replace(".obj",".png"),
  {
    name: `${baseName}.png`
  }
);

await archive.finalize();

console.log("✅ Minecraft Voxel Exported");
