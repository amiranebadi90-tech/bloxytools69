import path from "path";
import { getConfig } from "../utils/configUtils.js";
import { configPath } from "../utils/configUtils.js";
import { checkAndMkdir, resolveSpritePath } from "../utils/utils.js";
import { RUN_ID } from "../utils/runId.js";
function getPaths(type) {
  const configValues = getConfigValues();
  if (!configValues) {
    throw new Error("Couldn't fetch config - paths.");
  }
  let { bedrock, packFileName } = configValues;
  // اسم پوشه‌ی temp هم به RUN_ID وابسته‌ست تا اجراهای همزمان توی همدیگه
  // تداخل نکنن (قبلاً "temp" ثابت بود).
  const tempPath = path.join(process.cwd(), `.temp-${RUN_ID}`);
  let packSavePath = path.join(tempPath, packFileName);
  const guiFolderPath = bedrock === true ? path.join(packSavePath, "textures", "gui") : bedrock === false ? path.join(packSavePath, "assets", "minecraft", "textures", "gui") : bedrock === void 0 ? "" : "";

  // The expected location isn't always correct: some packs are missing the
  // file there, or (rarely) have a folder with that same name instead of a
  // file. resolveSpritePath validates the expected path and, if it's not a
  // usable file, searches the whole pack for the real one before giving up.
  const preferredIconsPath = path.join(guiFolderPath, "icons.png");
  const iconsPath = guiFolderPath
    ? resolveSpritePath(packSavePath, preferredIconsPath, ["icons.png"]) || preferredIconsPath
    : preferredIconsPath;

  const widgetsFileName = bedrock === true ? "gui.png" : bedrock === false ? "widgets.png" : "";
  const preferredWidgetsPath = widgetsFileName ? path.join(guiFolderPath, widgetsFileName) : "";
  const widgetsPath = widgetsFileName
    ? resolveSpritePath(packSavePath, preferredWidgetsPath, [widgetsFileName]) || preferredWidgetsPath
    : "";
  const iconsSavePath = path.join(tempPath, "icons");
  const widgetsSavePath = path.join(tempPath, "widgets");
  const _configPath = configPath;
  const sysPaths = {
    tempPath,
    packFolder: packSavePath,
    packGuiFolder: guiFolderPath,
    packIconsPath: iconsPath,
    packWidgetsPath: widgetsPath,
    tempIconsPath: iconsSavePath,
    tempWidgetsPath: widgetsSavePath,
    configPath: _configPath
  };
  if (type === "SYS") return sysPaths;
  const iconsPaths = {
    xpBg: path.join(iconsSavePath, "xpBg.png"),
    hungerBg: path.join(iconsSavePath, "hungerBg.png"),
    heartBg: path.join(iconsSavePath, "heartBg.png"),
    xp: path.join(iconsSavePath, "xp.png"),
    hunger: path.join(iconsSavePath, "hunger.png"),
    heart: path.join(iconsSavePath, "heart.png"),
    armor: path.join(iconsSavePath, "armor.png")
  };
  if (type === "ICON") return iconsPaths;
  const guiPaths = {
    twoSlots: path.join(widgetsSavePath, "firstTwoSlots.png"),
    lastSlot: path.join(widgetsSavePath, "lastSlot.png"),
    selector: path.join(widgetsSavePath, "selector.png")
  };
  if (type === "GUI") return guiPaths;
  throw new Error(`Invalid type: ${type}`);
}
function getConfigValues() {
  try {
    const config = getConfig();
    const bedrock = config.bedrock ?? void 0;
    const packFileName = config.packFileName ?? void 0;
    return { bedrock, packFileName };
  } catch (err) {
    console.error("Error fetching config: ", err);
    return void 0;
  }
}
// فقط کلیدهایی که واقعاً پوشه هستن باید mkdir بشن. قبلاً این تابع روی همه‌ی
// مقادیر sysPaths (از جمله packIconsPath/packWidgetsPath/configPath که مسیر
// فایل هستن، نه پوشه) checkAndMkdir صدا می‌زد. اگه فایل مورد نظر (مثلاً به‌خاطر
// یه پک ناقص/خراب یا تشخیص اشتباه bedrock) وجود نداشت، checkAndMkdir به‌جاش یه
// پوشه با همون اسم می‌ساخت؛ این هم اون فایل رو برای همیشه غیرقابل‌بازیابی
// می‌کرد و هم باعث می‌شد ارور واقعی («فایل پیدا نشد») به یه ارور گمراه‌کننده‌ی
// «یک فایل نیست، پوشه‌ای با همین اسم پیدا شد» تبدیل بشه.
const DIRECTORY_PATH_KEYS = /* @__PURE__ */ new Set([
  "tempPath",
  "packFolder",
  "packGuiFolder",
  "tempIconsPath",
  "tempWidgetsPath"
]);
function initializePaths(paths) {
  for (const pth in paths) {
    if (paths[pth] === void 0) continue;
    if (!DIRECTORY_PATH_KEYS.has(pth)) continue;
    try {
      checkAndMkdir(paths[pth]);
    } catch (err) {
      console.error("Error while initialising paths: " + err);
    }
  }
}
export {
  getPaths,
  initializePaths
};
