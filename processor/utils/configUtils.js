import fs from "fs";
import path from "path";
import { RUN_ID } from "./runId.js";
// اسم فایل کانفیگ به RUN_ID وابسته‌ست تا اجراهای همزمان (چند کاربر همزمان)
// روی فایل همدیگه ننویسن - قبلاً اسم ثابت "config.json" بود که باعث
// race condition بین پروسه‌های Node همزمان می‌شد.
const configPath = path.join(process.cwd(), `.config-${RUN_ID}.json`);
function createConfig() {
  if (fs.existsSync(configPath)) fs.rmSync(configPath);
  try {
    fs.writeFileSync(configPath, JSON.stringify({}));
  } catch (err) {
    throw new Error("Error while creating config: " + err);
  }
  return configPath;
}
function getConfig() {
  if (!configPath) return null;
  if (!fs.existsSync(configPath))
    throw new Error("Path does not exist: " + configPath);
  if (!fs.lstatSync(configPath).isFile())
    throw new Error("Path is not a file: " + configPath);
  const configJson = JSON.parse(fs.readFileSync(configPath).toString());
  return configJson;
}
function setValue(key, value, mode = "insert") {
  if (!configPath)
    throw new Error("Error while setting value: configPath non-existent");
  if (!fs.existsSync(configPath))
    throw new Error("Path does not exist: " + configPath);
  if (!fs.lstatSync(configPath).isFile())
    throw new Error("Path is not a file: " + configPath);
  let configJson = JSON.parse(fs.readFileSync(configPath).toString());
  if (mode === "insert" && key in configJson) {
    throw new Error(
      `Key "${key}" already exists. Use "modify" mode to update it.`
    );
  } else if (mode === "modify" && !(key in configJson)) {
    throw new Error(
      `Key "${key}" does not exist. Use "insert" mode to add it.`
    );
  }
  configJson[key] = value;
  try {
    fs.writeFileSync(configPath, JSON.stringify(configJson));
  } catch (err) {
    throw new Error("Failed to set value: " + err);
  }
  return;
}
function deleteValue(key) {
  if (!configPath) return null;
  if (!fs.existsSync(configPath))
    throw new Error("Path does not exist: " + configPath);
  if (!fs.lstatSync(configPath).isFile())
    throw new Error("Path is not a file: " + configPath);
  if (!key) {
    throw new Error("Invalid Parameters!");
  }
  let configJson = JSON.parse(
    fs.readFileSync(configPath).toString()
  );
  if (!(key in configJson)) {
    throw new Error(`Key "${key}" does not exist in the configuration.`);
  }
  const { [key]: _, ...newConfigJson } = configJson;
  try {
    fs.writeFileSync(configPath, JSON.stringify(newConfigJson));
  } catch (err) {
    throw new Error("Failed to delete value: " + err);
  }
  return;
}
export {
  configPath,
  createConfig,
  deleteValue,
  getConfig,
  setValue
};
