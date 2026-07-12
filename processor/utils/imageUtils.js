import { createCanvas, loadImage } from "@napi-rs/canvas";
import fs from "fs";
import sharp from "sharp";
async function upscaleImage(input, scale) {
  const image = sharp(input);
  if (image.errored) {
    throw image.errored;
  }
  const metadata = await image.metadata();
  const width = metadata.width * scale;
  const height = metadata.height * scale;
  const upscaledBuffer = await image.resize(width, height, {
    kernel: sharp.kernel.nearest
  }).toBuffer();
  return upscaledBuffer;
}
function calculateCanvasSize(icons) {
  let maxWidth = 0;
  let maxHeight = 0;
  for (const icon of icons) {
    const { x, y, width, height } = icon.destCoordinates;
    maxWidth = Math.max(maxWidth, x + width);
    maxHeight = Math.max(maxHeight, y + height);
  }
  return { width: maxWidth, height: maxHeight };
}
async function combineIcons(icons) {
  const { width, height } = calculateCanvasSize(icons);
  const canvas = createCanvas(width, height);
  const ctx = canvas.getContext("2d");
  for (const icon of icons) {
    const image = await loadImage(icon.path);
    ctx.drawImage(
      image,
      icon.destCoordinates.x,
      icon.destCoordinates.y,
      image.width,
      image.height
    );
  }
  return canvas;
}
async function cropIcon(spriteSheetPath, outputPath, x, y, width, height) {
  try {
    const spriteImage = await loadImage(spriteSheetPath);
    const spriteSize = {
      width,
      height
    };
    const canvas = createCanvas(spriteSize.width, spriteSize.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(
      spriteImage,
      x,
      y,
      spriteSize.width,
      spriteSize.height,
      0,
      0,
      spriteSize.width,
      spriteSize.height
    );
    const iconBuffer = canvas.toBuffer("image/png");
    savePngBuffer(iconBuffer, outputPath);
    return;
  } catch (err) {
    throw new Error("Error while cropping image: " + err);
  }
}
async function repeatIcon(imagePath, times) {
  const image = await loadImage(imagePath);
  const canvas = createCanvas(image.width * times, image.height);
  const context = canvas.getContext("2d");
  for (let i = 0; i <= times; i++) {
    context.drawImage(image, image.width * i, 0);
  }
  const repeatedIconBuffer = canvas.toBuffer("image/png");
  return repeatedIconBuffer;
}
function savePngBuffer(buffer, outputPath) {
  try {
    fs.writeFileSync(outputPath, buffer);
  } catch (err) {
    console.error("Error while saving PNG buffer: " + err);
    return;
  }
}
export {
  combineIcons,
  cropIcon,
  repeatIcon,
  savePngBuffer,
  upscaleImage
};
