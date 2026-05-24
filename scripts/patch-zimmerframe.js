// patch-zimmerframe.js -- postinstall fix for zimmerframe 1.1.x
//
// zimmerframe (dep of @preact/preset-vite) only declares an "import"
// exports entry with no "default" or "main". Vite's transform-hook-names
// plugin fails to resolve it. This script adds the missing entries.

const fs = require('fs');
const path = require('path');

const pkgPath = path.join(__dirname, '..', 'node_modules', 'zimmerframe', 'package.json');

if (!fs.existsSync(pkgPath)) {
  // zimmerframe not installed yet (first install) -- skip silently
  process.exit(0);
}

const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));

let changed = false;

if (pkg.exports && pkg.exports['.'] && !pkg.exports['.'].default) {
  pkg.exports['.'].default = './src/walk.js';
  changed = true;
}

if (!pkg.main) {
  pkg.main = './src/walk.js';
  changed = true;
}

if (changed) {
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
  console.log('  [patch] zimmerframe: added "default" export + "main" entry');
}
