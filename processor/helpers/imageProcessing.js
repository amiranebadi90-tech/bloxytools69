import fs from "fs";
import { getPaths } from "./paths.js";
import {
  combineIcons,
  cropIcon,
  repeatIcon,
  savePngBuffer,
  upscaleImage
} from "../utils/imageUtils.js";
import { getCoordinates, getDestinationCoordinates } from "./coordinates.js";
import { loadImage } from "@napi-rs/canvas";
import { getConfig } from "../utils/configUtils.js";
async function crop(type) {
  const systemPaths = getPaths("SYS");
  const imagePaths = getPaths(type);
  const imageCoordinates = getCoordinates(type);
  const spriteSheetPath = type === "ICON" ? systemPaths.packIconsPath : type === "GUI" ? systemPaths.packWidgetsPath : void 0;
  if (!spriteSheetPath) {
    throw new Error("Invalid Type: " + type);
  }
  const imagePathEntries = Object.entries(imagePaths);
  const imageCoordinateEntries = Object.entries(imageCoordinates);
  for (let i = 0; i < imagePathEntries.length; i++) {
    const [_, path] = imagePathEntries[i];
    const [__, coordinates] = imageCoordinateEntries[i];
    await cropIcon(
      spriteSheetPath,
      path,
      coordinates.x,
      coordinates.y,
      coordinates.width,
      coordinates.height
    );
  }
  return;
}
async function combine() {
  try {
    const iconsInfo = generateIconInfo("ICON");
    const guiInfo = generateIconInfo("GUI");
    let repeatImageKeys = ["heart", "armor", "hunger"];
    repeatImageKeys.push(...repeatImageKeys.map((item) => `${item}Bg`));
    for (const iconInfo of iconsInfo) {
      if (repeatImageKeys.includes(iconInfo.name)) {
        iconInfo.destCoordinates.width *= 3;
        const iconBuffer = await repeatIcon(iconInfo.path, 3);
        iconInfo.path = iconBuffer;
      }
    }
    const iconsCanvas = await combineIcons(iconsInfo);
    const guiCanvas = await combineIcons(guiInfo);
    const finalIconsInfo = {
      name: "iconspng",
      path: iconsCanvas.toBuffer("image/png"),
      destCoordinates: {
        x: 0,
        y: 0,
        width: iconsCanvas.width,
        height: iconsCanvas.height
      }
    };
    const finalGuiInfo = {
      name: "guipng",
      path: guiCanvas.toBuffer("image/png"),
      destCoordinates: {
        x: 0,
        y: iconsCanvas.height,
        width: guiCanvas.width,
        height: guiCanvas.height
      }
    };
    const finalUiCanvas = await combineIcons([
      finalIconsInfo,
      finalGuiInfo
    ]);
    return finalUiCanvas.toBuffer("image/png");
  } catch (error) {
    console.error("Error in combine function:", error);
    return;
  }
}
function generateIconInfo(type) {
  const imagePaths = getPaths(type);
  const destinationCoordinates = getDestinationCoordinates(type);
  const imagePathEntries = Object.entries(imagePaths);
  const imageCoordinateEntries = Object.entries(destinationCoordinates);
  const iconsInfo = [];
  for (let i = 0; i < imagePathEntries.length; i++) {
    const [_, path] = imagePathEntries[i];
    const [name, coordinates] = imageCoordinateEntries[i];
    const icon = {
      name,
      path,
      destCoordinates: {
        x: coordinates.x,
        y: coordinates.y,
        width: coordinates.width,
        height: coordinates.height
      }
    };
    iconsInfo.push(icon);
  }
  return iconsInfo;
}
async function processImage() {
  const uiBuffer = await combine();
  const upscaleRate = getUpscaleRate();
  let finalImageBuffer = Buffer.from("");
  if (upscaleRate > 1) {
    finalImageBuffer = await upscaleImage(uiBuffer, 10);
  } else {
    finalImageBuffer = uiBuffer;
  }
  return finalImageBuffer;
}
function getUpscaleRate() {
  try {
    const config = getConfig();
    const upscaleRate = config.upscaleRate;
    return upscaleRate;
  } catch (err) {
    throw new Error("Error fetching config: " + err);
  }
}
function assertValidSpriteFile(label, filePath) {
  if (!filePath || !fs.existsSync(filePath)) {
    throw new Error(
      `پیدا نشد: ${label} در مسیر "${filePath}". این پک ساختار غیرمعمول یا خرابی دارد (فایل مورد نظر وجود ندارد).`
    );
  }
  if (!fs.lstatSync(filePath).isFile()) {
    throw new Error(
      `${label} در مسیر "${filePath}" یک فایل نیست (پوشه‌ای با همین نام پیدا شد). این پک ساختار غیرمعمول یا خراب دارد.`
    );
  }
}

async function imageDimsFix(iconsPath, widgetsPath) {
  assertValidSpriteFile("icons.png", iconsPath);
  assertValidSpriteFile("gui.png/widgets.png", widgetsPath);
  const widgetsDims = {
    width: (await loadImage(widgetsPath)).width,
    height: (await loadImage(widgetsPath)).height
  };
  const iconsDims = {
    width: (await loadImage(iconsPath)).width,
    height: (await loadImage(iconsPath)).height
  };
  if (widgetsDims.width === iconsDims.width) {
    return;
  } else if (widgetsDims.width < iconsDims.width) {
    const imageBuffer = await upscaleImage(
      widgetsPath,
      ~~(iconsDims.width / widgetsDims.width)
    );
    savePngBuffer(imageBuffer, widgetsPath);
  } else if (iconsDims.width < widgetsDims.width) {
    const imageBuffer = await upscaleImage(
      iconsPath,
      ~~(widgetsDims.width / iconsDims.width)
    );
    savePngBuffer(imageBuffer, iconsPath);
  } else {
    throw new Error(
      `Error while fixing dimentions.
WidgetsWidth = ${widgetsDims.width}
IconsWidth = ${iconsDims.width}`
    );
  }
}
export {
  crop,
  imageDimsFix,
  processImage
};
