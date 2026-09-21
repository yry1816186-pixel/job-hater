// popup：连接本机 Job Hater → 抓取 JD 草稿（可编辑后入库）/ 表单填充（只填不提交）。
/* global chrome */

const $ = (id) => document.getElementById(id)
let apiBase = "http://127.0.0.1:8787"
let profiles = []
let activeProfileId = null

async function api(path, init) {
  const resp = await fetch(`${apiBase}/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try {
      const body = await resp.json()
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(detail)
  }
  return resp.json()
}

async function inject(fn, ...args) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  if (!tab?.id) throw new Error("没有活动标签页")
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["content/assistant.js"],
  })
  void results
  return chrome.tabs.sendMessage(tab.id, { type: fn, ...args[0] })
}

async function setStatus(ok, text) {
  $("dot").className = `dot ${ok ? "ok" : "bad"}`
  $("status").textContent = text
  $("capture").disabled = !ok
  $("fill").disabled = !ok || profiles.length === 0
}

async function loadProfiles() {
  profiles = await api("/profiles")
  const sel = $("profileSel")
  sel.innerHTML = ""
  for (const p of profiles) {
    const o = document.createElement("option")
    o.value = p.id
    o.textContent = `${p.display_name}${p.headline ? " · " + p.headline : ""}`
    sel.appendChild(o)
  }
  const saved = (await chrome.storage.local.get(["profileId"])).profileId
  activeProfileId = profiles.some((p) => p.id === saved) ? saved : profiles[0]?.id ?? null
  if (activeProfileId) sel.value = activeProfileId
}

async function boot() {
  $("ver").textContent = "v" + chrome.runtime.getManifest().version
  const saved = await chrome.storage.local.get(["apiBase"])
  apiBase = saved.apiBase || "http://127.0.0.1:8787"
  $("apiBase").value = apiBase
  try {
    await api("/health")
    await loadProfiles()
    await setStatus(true, `已连接 ${apiBase} · ${profiles.length} 个画像`)
  } catch (e) {
    await setStatus(false, `连不上 ${apiBase}：${e.message}。请先启动 Job Hater（job-hater serve）。`)
  }
}

$("apiBase").addEventListener("change", async () => {
  apiBase = $("apiBase").value.replace(/\/$/, "")
  await chrome.storage.local.set({ apiBase })
  await boot()
})
$("profileSel").addEventListener("change", async () => {
  activeProfileId = $("profileSel").value
  await chrome.storage.local.set({ profileId: activeProfileId })
})

/* ---------- JD 抓取 → 草稿（可编辑）→ 入库 ---------- */

$("capture").addEventListener("click", async () => {
  $("draftArea").innerHTML = '<div class="muted">抓取中…</div>'
  try {
    const jd = await inject("CAPTURE_JD")
    $("draftArea").innerHTML = `
      <div class="draft">
        <input id="dTitle" placeholder="岗位标题" />
        <input id="dCompany" placeholder="公司" />
        <input id="dCity" placeholder="城市（可空）" />
        <button id="dSave" class="primary">存入岗位库（先出草稿可再改）</button>
        <div class="muted" id="dMeta" style="margin-top:4px">正文 ${jd.text.length} 字 · 来自 ${jd.url.slice(0, 60)}</div>
        <div class="ok-box" id="dMsg"></div>
      </div>`
    $("dTitle").value = jd.title || ""
    $("dCompany").value = jd.company || ""
    $("dSave").addEventListener("click", async () => {
      const title = $("dTitle").value.trim()
      const company = $("dCompany").value.trim()
      if (!title || !company) {
        $("dMsg").className = "err-box"
        $("dMsg").textContent = "入库需要标题和公司——补全后再存。"
        return
      }
      try {
        // 与 Web 端同一入口：保存为岗位（manual 信源），城市/薪资等入库后可再改
        const r = await api("/jobs/import", {
          method: "POST",
          body: JSON.stringify({
            source_id: "manual",
            jobs: [
              {
                title,
                company,
                city: $("dCity").value.trim() || undefined,
                description: jd.text,
                url: jd.url,
              },
            ],
          }),
        })
        $("dMsg").className = "ok-box"
        $("dMsg").textContent = `已入库：新增 ${r.added} 条（去重 ${r.deduped_exact + r.deduped_near} 条）。去「岗位收件箱」查看与匹配。`
      } catch (e) {
        $("dMsg").className = "err-box"
        $("dMsg").textContent = `入库失败：${e.message}`
      }
    })
  } catch (e) {
    $("draftArea").innerHTML = `<div class="err-box">抓取失败：${e.message}</div>`
  }
})

/* ---------- 表单填充（只填不提交） ---------- */

$("fill").addEventListener("click", async () => {
  $("fillReport").innerHTML = '<div class="muted">扫描表单中…</div>'
  try {
    const pid = activeProfileId || profiles[0]?.id
    // GET /profiles/{id} 返回完整画像视图（profile + 各分节）
    const view = await api(`/profiles/${pid}`)
    const p = view.profile ?? {}
    const edu = view.educations?.[0] ?? {}
    const profile = {
      name: p.display_name ?? "",
      phone: p.phone ?? "",
      email: p.email ?? "",
      wechat: "",
      school: edu.school ?? "",
      degree: edu.degree ?? "",
      major: edu.major ?? "",
      graduation: edu.end_date ?? "",
      city: "",
      summary: p.summary ?? "",
    }
    const report = await inject("FILL_FORM", { profile })
    const li = (x) => `<li>${x.label || x.field || ""}${x.reason ? " · " + x.reason : ""}</li>`
    $("fillReport").innerHTML = `
      <div class="fill-report">
        扫描 ${report.scanned} 个字段：填充 <b>${report.filled.length}</b> 个（橙框高亮，请逐一核对），
        跳过 ${report.skipped.length} 个。
        ${report.filled.length ? `<details><summary>已填充</summary><ul>${report.filled.map(li).join("")}</ul></details>` : ""}
        ${report.skipped.length ? `<details><summary>跳过</summary><ul>${report.skipped.map(li).join("")}</ul></details>` : ""}
        <p class="muted" style="margin-top:6px">提交前请人工核对全部字段——本扩展永不代你提交。</p>
      </div>`
  } catch (e) {
    $("fillReport").innerHTML = `<div class="err-box">填充失败：${e.message}</div>`
  }
})

boot()
