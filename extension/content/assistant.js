// 内容脚本：JD 抓取 + 表单基础信息填充（由 popup 经 chrome.scripting 注入，按需执行）。
// 诚实原则：只读取页面、只填充可见字段并高亮，绝不触发提交、绝不外发页面数据
// （数据只回传给本机 Job Hater API 或展示在 popup 里）。

/* ---------- JD 抓取 ---------- */

function captureJD() {
  const meta = (name) =>
    document.querySelector(`meta[property="${name}"]`)?.content ||
    document.querySelector(`meta[name="${name}"]`)?.content || ""

  // 取"主内容区"的启发式：article/main/最大的文本块；退化到 body.innerText
  const candidates = [...document.querySelectorAll("article, main, [class*='job'], [class*='job-desc'], [class*='description']")]
    .filter((el) => el.innerText && el.innerText.length > 200)
    .sort((a, b) => b.innerText.length - a.innerText.length)
  const text = (candidates[0]?.innerText || document.body.innerText || "").slice(0, 20000)

  return {
    title: document.title.replace(/[|｜\-—].*$/, "").trim().slice(0, 120),
    company: meta("og:site_name") || meta("author") || location.hostname.replace(/^www\./, ""),
    url: location.href,
    text,
    capturedAt: new Date().toISOString(),
  }
}

/* ---------- 表单填充（中国网申基础字段；只填不提交） ---------- */

const FILL_RULES = [
  { keys: ["姓名", "名字", "name"], field: "name", type: "text" },
  { keys: ["手机", "手机号", "电话", "联系方式", "mobile", "phone", "tel"], field: "phone", type: "text" },
  { keys: ["邮箱", "电子邮件", "email", "mail"], field: "email", type: "text" },
  { keys: ["微信", "wechat"], field: "wechat", type: "text" },
  { keys: ["学校", "院校", "毕业院校", "university", "school"], field: "school", type: "text" },
  { keys: ["学历", "学位", "degree"], field: "degree", type: "text" },
  { keys: ["专业", "所学专业", "major"], field: "major", type: "text" },
  { keys: ["毕业时间", "毕业年月", "毕业日期"], field: "graduation", type: "text" },
  { keys: ["所在城市", "期望城市", "现居城市", "城市"], field: "city", type: "text" },
  { keys: ["自我介绍", "个人简介", "自我评价"], field: "summary", type: "textarea" },
]

function labelOf(el) {
  // 就近标签：label[for] → 父级 label → 前置文本 → aria-label → placeholder
  if (el.id) {
    const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    if (l) return l.innerText
  }
  let p = el.closest("label")
  if (p) return p.innerText
  const prev = el.previousElementSibling
  if (prev && prev.innerText && prev.innerText.length < 30) return prev.innerText
  const row = el.closest("div,td,li")
  if (row) {
    const t = row.innerText
    if (t && t.length < 40) return t
  }
  return el.getAttribute("aria-label") || el.placeholder || ""
}

function fillForm(profile) {
  const filled = []
  const skipped = []
  const inputs = [...document.querySelectorAll("input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]):not([type=password]), textarea, select")]
  for (const el of inputs) {
    if (el.disabled || el.readOnly) continue
    if (el.value && el.value.trim()) continue // 已有内容不覆盖（用户已填的优先）
    const labelText = (labelOf(el) + " " + (el.name || "") + " " + (el.id || "")).toLowerCase()
    for (const rule of FILL_RULES) {
      const value = profile[rule.field]
      if (value == null || value === "") continue
      if (!rule.keys.some((k) => labelText.includes(k.toLowerCase()))) continue
      if (el instanceof HTMLSelectElement) {
        const opt = [...el.options].find(
          (o) => o.text.includes(String(value)) || String(value).includes(o.text),
        )
        if (opt) {
          el.value = opt.value
          el.dispatchEvent(new Event("change", { bubbles: true }))
          filled.push({ label: labelText.trim().slice(0, 24), field: rule.field })
        } else {
          skipped.push({ label: labelText.trim().slice(0, 24), reason: "下拉没有匹配选项" })
        }
      } else {
        el.value = String(value)
        el.dispatchEvent(new Event("input", { bubbles: true }))
        el.dispatchEvent(new Event("change", { bubbles: true }))
        filled.push({ label: labelText.trim().slice(0, 24), field: rule.field })
      }
      el.style.outline = "2px solid #e6963c" // 高亮已填充项，供人工核对
      el.title = "Job Hater 已填充（请核对）"
      break
    }
  }
  return { filled, skipped, scanned: inputs.length }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "CAPTURE_JD") {
    sendResponse(captureJD())
    return
  }
  if (msg.type === "FILL_FORM") {
    sendResponse(fillForm(msg.profile))
    return
  }
})
