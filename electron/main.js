// Linked Buddy desktop shell (Windows, macOS, Linux, Raspberry Pi)
const { app, BrowserWindow, shell, session, Menu } = require('electron');
const path = require('path');

const args = process.argv.slice(1);
const KIOSK = args.includes('--kiosk');
// Raspberry Pi and some older graphics chips draw more reliably without GPU compositing.
if (args.includes('--no-gpu') || process.env.APP_NO_GPU === '1') app.disableHardwareAcceleration();

function createWindow() {
  const win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 360, minHeight: 480,
    backgroundColor: '#0b1220', title: 'Linked Buddy', autoHideMenuBar: true, kiosk: KIOSK, fullscreen: KIOSK,
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.loadFile(path.join(__dirname, '..', 'www', 'index.html'));
  // Links open in the normal web browser.
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:\/\//.test(url)) shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith('file://')) { e.preventDefault(); if (/^https?:\/\//.test(url)) shell.openExternal(url); } });
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'clipboard-sanitized-write'));
  if (process.platform !== 'darwin') Menu.setApplicationMenu(null);
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
