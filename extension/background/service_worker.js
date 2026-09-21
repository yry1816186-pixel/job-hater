// 后台 service worker：当前只做安装时初始化（默认 API 地址）。
// 通信都在 popup ↔ content script ↔ 本机 API 之间直连，后台不转发数据。
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(["apiBase"], (r) => {
    if (!r.apiBase) chrome.storage.local.set({ apiBase: "http://127.0.0.1:8787" })
  })
})
