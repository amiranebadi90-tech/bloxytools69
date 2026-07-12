// processor/world_to_obj.mjs
import { execFile } from 'child_process';
import path from 'path';
import fs from 'fs/promises';

const __dirname = path.dirname(new URL(import.meta.url).pathname);
const JAR_PATH = path.join(__dirname, 'jmc2obj.jar');

async function exportWorld(worldPath, outputBase, options = {}) {
  // Ensure Java is available
  const args = [
    '-jar', JAR_PATH,
    '-world', worldPath,
    '-o', outputBase,
    // Default options for reasonable export
    '-minx', options.minX || '-64',
    '-maxx', options.maxX || '64',
    '-minz', options.minZ || '-64',
    '-maxz', options.maxZ || '64',
    '-miny', options.minY || '0',
    '-maxy', options.maxY || '100',
    '-separate', // separate objects per material
    '-biomes', // include biomes if possible
  ];

  return new Promise((resolve, reject) => {
    execFile('java', args, { cwd: __dirname, timeout: 300000 }, (error, stdout, stderr) => {
      if (error) {
        console.error('jMC2Obj error:', stderr);
        reject(new Error(`World export failed: ${stderr || error.message}`));
        return;
      }
      console.log('jMC2Obj output:', stdout);
      resolve(outputBase + '.obj');
    });
  });
}

export default exportWorld;
