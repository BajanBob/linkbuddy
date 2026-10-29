const { contextBridge } = require('electron');
contextBridge.exposeInMainWorld('appNative', { platform: process.platform, electron: true });
