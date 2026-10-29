// Adds permissions to the generated Android and iOS projects.
// Usage: node scripts/patch-native.js android|ios
const fs = require('fs');
const path = require('path');
const which = process.argv[2];
const ANDROID = ["INTERNET"];
if (which === 'android') {
  const f = path.join('android', 'app', 'src', 'main', 'AndroidManifest.xml');
  let x = fs.readFileSync(f, 'utf8');
  for (const p of ANDROID) if (!x.includes('android.permission.' + p + '"')) x = x.replace('</manifest>', '    <uses-permission android:name="android.permission.' + p + '" />\n</manifest>');
  fs.writeFileSync(f, x); console.log('Patched', f);
} else if (which === 'ios') {
  const f = path.join('ios', 'App', 'App', 'Info.plist');
  let x = fs.readFileSync(f, 'utf8');
  const add = [
    ['NSPhotoLibraryUsageDescription', 'Linked Buddy opens pictures you choose.'],
    ['NSPhotoLibraryAddUsageDescription', 'Linked Buddy saves files you choose to keep.'],
  ];
  for (const [k, v] of add) if (!x.includes(k)) { const i = x.lastIndexOf('</dict>'); x = x.slice(0, i) + '\t<key>' + k + '</key>\n\t<string>' + v + '</string>\n' + x.slice(i); }
  fs.writeFileSync(f, x); console.log('Patched', f);
} else { console.error('Usage: node scripts/patch-native.js android|ios'); process.exit(1); }
