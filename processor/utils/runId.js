// processor/utils/runId.js
//
// هر بار که processor.mjs اجرا می‌شه یه پروسه‌ی Node جدید و جدا است، اما همیشه
// با همون cwd ثابت (پوشه‌ی processor) اجرا می‌شه (بات پایتون این‌طوری صداش می‌زنه).
// قبلاً configPath و tempPath هر دو اسم ثابت داشتن (config.json و temp/) که یعنی
// اگه دو نفر همزمان پک بفرستن، دو پروسه‌ی Node مجزا دقیقاً روی همون فایل/پوشه
// می‌نوشتن و می‌خوندن و مقادیر همدیگه رو (مثلاً bedrock=true/false و packFileName)
// overwrite می‌کردن. همین باعث می‌شد گاهی یه پک واقعاً Bedrock با مسیر Java
// پردازش بشه (یا برعکس) و به خطای ENOENT بخوره.
//
// RUN_ID با pid + یه تایم‌استمپ با دقت بالا ساخته می‌شه تا هر پروسه‌ی Node
// یکتا باشه و config/temp خودش رو داشته باشه.
const RUN_ID = `${process.pid}_${process.hrtime.bigint()}`;

export { RUN_ID };
