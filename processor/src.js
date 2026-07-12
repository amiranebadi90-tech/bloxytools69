// processor/src.js
import fs from "fs";
import { configPath, createConfig, setValue } from "./utils/configUtils.js";
import { getPaths, initializePaths } from "./helpers/paths.js";
import {
    checkBedrock,
    clean,
    convertBedrock,
    getScale,
    unzipFile,
    findGuiSprite,
} from "./utils/utils.js";
import { crop, imageDimsFix, processImage } from "./helpers/imageProcessing.js";

async function initialize(
    packFileName,
    packZipBuffer,
    upscaleRate,
    xpPercent
) {
    createConfig();

    try {
        setValue("packFileName", packFileName, "insert");

        const folderPaths = getPaths("SYS");

        if (fs.existsSync(folderPaths.packFolder)) {
            fs.rmSync(folderPaths.packFolder, { recursive: true, force: true });
        }

        await unzipFile(packZipBuffer, folderPaths.packFolder);

        console.log("📦 Unzip completed. Root:", fs.readdirSync(folderPaths.packFolder));

        const bedrock = checkBedrock(folderPaths.packFolder);
        if (bedrock) convertBedrock(folderPaths.packFolder);

        setValue("bedrock", bedrock, "insert");

        initializePaths(getPaths("SYS"));

        // === بخش جدید: پیدا کردن icons.png هوشمند ===
        const iconsPath = findGuiSprite(folderPaths.packFolder);
        
        if (!iconsPath) {
            console.error("❌ Could not find any icons.png / gui.png in the pack!");
            throw new Error("Missing sprite sheet: icons.png (tried multiple paths)");
        }

        console.log("✅ Found sprite sheet at:", iconsPath);

        const scalingFactor = await getScale(iconsPath);
        setValue("scalingFactor", scalingFactor, "insert");

        setValue("upscaleRate", upscaleRate, "insert");
        setValue("xpPercent", xpPercent, "insert");

    } catch (err) {
        console.error("Initialization failed:", err.message || err);
        throw new Error("Error while initialising: " + (err.message || err));
    }
}

export default async function main(
    packName,
    packZipBuffer,
    upscaleRate,
    xpPercent
) {
    await initialize(packName, packZipBuffer, upscaleRate, xpPercent);

    const systemPaths = getPaths("SYS");

    // Fix image dimensions
    try {
        await imageDimsFix(
            systemPaths.packIconsPath,
            systemPaths.packWidgetsPath
        );
    } catch (err) {
        throw new Error(err.toString());
    }

    // Crop icons and GUI
    await crop("ICON");
    await crop("GUI");

    // Process final UI image
    const uiImageBuffer = await processImage();

    // Cleanup
    clean([systemPaths.tempPath, configPath]);

    return uiImageBuffer;
}
