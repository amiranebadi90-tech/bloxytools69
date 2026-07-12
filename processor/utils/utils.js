import fs from "fs";
import unzipper from "unzipper";
import path from "path";
import { loadImage } from "@napi-rs/canvas";
// قبلاً از unzipper.Extract() (استریمی) استفاده می‌شد. باگ: رویداد "close"
// قبل از تموم شدن نوشتن همه‌ی entry ها روی دیسک فایر می‌شد (مشکل شناخته‌شده‌ی
// این کتابخونه با zip هایی که تعداد entry زیادی دارن)، و هیچ خطایی هم پرتاب
// نمی‌شد. نتیجه: پک‌های .mcpack به‌صورت ناقص extract می‌شدن (مثلاً فقط پوشه‌ی
// textures استخراج می‌شد و manifest.json از قلم می‌افتاد)، checkBedrock به‌خاطر
// نبود manifest.json در جای درست، پک رو Bedrock تشخیص نمی‌داد و مسیرهای Java
// (assets/minecraft/textures/gui/...) به‌جای مسیرهای Bedrock (textures/gui/...)
// استفاده می‌شدن که باعث خطای "gui.png/widgets.png ... یک فایل نیست" می‌شد.
//
// راه‌حل: به‌جای stream + رویداد close، از unzipper.Open.buffer استفاده می‌کنیم
// و هر entry رو کامل و به‌صورت await‌شده می‌خونیم و می‌نویسیم، تا مطمئن بشیم
// extract واقعاً کامل شده قبل از resolve شدن Promise.
async function unzipFile(zipFileBuffer, outputDir) {
  const directory = await unzipper.Open.buffer(zipFileBuffer);
  const resolvedOutputDir = path.resolve(outputDir);
  for (const entry of directory.files) {
    // جلوگیری از zip-slip (entry هایی با مسیر ../ که می‌تونن بیرون از
    // outputDir بنویسن)
    const entryPath = path.resolve(resolvedOutputDir, entry.path);
    if (
      entryPath !== resolvedOutputDir &&
      !entryPath.startsWith(resolvedOutputDir + path.sep)
    ) {
      throw new Error(`Unsafe zip entry path: ${entry.path}`);
    }
    if (entry.type === "Directory") {
      fs.mkdirSync(entryPath, { recursive: true });
      continue;
    }
    fs.mkdirSync(path.dirname(entryPath), { recursive: true });
    const content = await entry.buffer();
    fs.writeFileSync(entryPath, content);
  }
  return;
}
function clean(paths) {
  for (const pth of paths) {
    if (!fs.existsSync(pth)) continue;
    fs.rmSync(pth, { recursive: true, force: true });
  }
  return;
}
async function getScale(spriteSheetPath) {
  if (!fs.existsSync(spriteSheetPath))
    throw new Error("Path does not exist: " + spriteSheetPath);
  return ~~((await loadImage(spriteSheetPath)).height / 256);
}
function checkAndMkdir(folderPath) {
  if (fs.existsSync(folderPath)) return;
  try {
    // recursive:true هم پوشه‌های والد رو در صورت نبودن می‌سازه و هم اگه
    // پوشه از قبل وجود داشت خطا نمی‌ده. بدون این، مسیرهای چند سطحی که
    // پوشه‌ی والدشون هنوز وجود نداره (مثل مسیر Java روی یه پک Bedrock) با
    // ENOENT می‌ترکیدن.
    fs.mkdirSync(folderPath, { recursive: true });
  } catch (err) {
    throw new Error("Error while creating directory: " + err);
  }
  return;
}
function checkBedrock(packPath) {
  let bedrock = false;
  if (!fs.existsSync(packPath))
    throw new Error("Path does not exist: " + packPath);
  if (!fs.lstatSync(packPath).isDirectory())
    throw new Error("Path is not a directory: " + packPath);
  if (packPath.split(".").pop() === "mcpack" && fs.readdirSync(packPath).includes("manifest.json")) {
    bedrock = true;
  }
  try {
    let subfiles = fs.readdirSync(packPath);
    if (subfiles.length <= 1) packPath = path.join(packPath, subfiles[0]);
    if (fs.readdirSync(packPath).includes("manifest.json")) bedrock = true;
  } catch (err) {
    throw new Error("Error while reading directory: " + err);
  }
  return bedrock;
}
function convertBedrock(parentDir) {
  if (!fs.existsSync(parentDir)) {
    console.error(`Source directory does not exist: ${parentDir}`);
    return;
  }
  const subfiles = fs.readdirSync(parentDir);
  if (subfiles.length === 1) {
    const subfolder = path.join(parentDir, subfiles[0]);
    if (fs.statSync(subfolder).isDirectory()) {
      const subfolderContents = fs.readdirSync(subfolder);
      for (const item of subfolderContents) {
        const itemPath = path.join(subfolder, item);
        const targetPath = path.join(parentDir, item);
        try {
          fs.renameSync(itemPath, targetPath);
        } catch (error) {
          console.error(
            `Error moving ${itemPath} to ${targetPath}:`,
            error
          );
        }
      }
      try {
        fs.rmdirSync(subfolder);
      } catch (error) {
        if (error.split(" ")[0] === "ENOTEMPTY") {
          console.warn(
            `Directory not empty after moving contents: ${subfolder}`
          );
        } else {
          console.error(
            `Error removing directory ${subfolder}:`,
            error
          );
        }
      }
    } else {
      console.error(
        `The only item in the directory is not a folder: ${subfolder}`
      );
    }
  }
  return;
}
function findFileRecursive(dir, targetNames) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    return null;
  }

  for (const entry of entries) {
    if (entry.isFile() && targetNames.includes(entry.name.toLowerCase())) {
      return path.join(dir, entry.name);
    }
  }

  for (const entry of entries) {
    if (entry.isDirectory()) {
      const found = findFileRecursive(path.join(dir, entry.name), targetNames);
      if (found) return found;
    }
  }

  return null;
}

// Recursively searches the (unzipped) pack folder for the icon sprite sheet,
// preferring "icons.png" (Java layout) and falling back to "gui.png" (Bedrock layout).
function findGuiSprite(packFolder) {
  if (!fs.existsSync(packFolder)) {
    throw new Error("Path does not exist: " + packFolder);
  }

  return (
    findFileRecursive(packFolder, ["icons.png"]) ||
    findFileRecursive(packFolder, ["gui.png"]) ||
    null
  );
}

// Some packs are malformed: the "expected" path (e.g. textures/gui/gui.png)
// can be missing, or can even be a *folder* instead of a file (this happens
// with some broken/re-packed texture packs). Rather than blindly trusting the
// hardcoded expected path, this checks that it's actually a usable file first,
// and if not, falls back to a recursive search for the real file anywhere in
// the pack. Returns null if no valid file is found anywhere.
function resolveSpritePath(packFolder, preferredPath, fileNames) {
  if (
    preferredPath &&
    fs.existsSync(preferredPath) &&
    fs.lstatSync(preferredPath).isFile()
  ) {
    return preferredPath;
  }

  return findFileRecursive(packFolder, fileNames);
}

export {
  checkAndMkdir,
  checkBedrock,
  clean,
  convertBedrock,
  findGuiSprite,
  getScale,
  resolveSpritePath,
  unzipFile
};
