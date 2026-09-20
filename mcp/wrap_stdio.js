#!/usr/bin/env node
/**
 * wrap_stdio.js — MCP stdio 卫生包装器
 *
 * 问题：部分第三方 MCP server（如实测：mcp-jobs v1.4.0）把爬虫日志直接打到 stdout，
 * 污染 JSON-RPC 流导致 MCP 客户端解析失败。
 * 方案：包装子进程，逐行过滤 stdout——只放行能解析为 JSON 的行（JSON-RPC 消息必然是 JSON），
 * 其余行改道 stderr 留档。stderr 原样透传。
 *
 * 用法：node wrap_stdio.js <真实命令> [参数...]
 * 返回码：透传子进程返回码。
 */
const { spawn } = require("child_process");
const readline = require("readline");

const [cmd, ...args] = process.argv.slice(2);
if (!cmd) {
  console.error("usage: node wrap_stdio.js <cmd> [args...]");
  process.exit(2);
}

const child = spawn(cmd, args, { stdio: ["pipe", "pipe", "pipe"] });

// stdin：客户端 → 子进程，原样透传
process.stdin.pipe(child.stdin);

// stdout：只放行 JSON 行
const rl = readline.createInterface({ input: child.stdout });
rl.on("line", (line) => {
  const t = line.trim();
  if (!t) return;
  if (t.startsWith("{")) {
    try {
      JSON.parse(t);
      process.stdout.write(t + "\n");
      return;
    } catch {
      /* 不完整或非 JSON 行 → 落到 stderr */
    }
  }
  process.stderr.write("[mcp-jobs noise] " + line + "\n");
});

// stderr：原样透传（保留其诊断信息）
child.stderr.pipe(process.stderr);

// 退出码与信号透传
child.on("exit", (code, sig) => {
  if (sig) process.kill(process.pid, sig);
  else process.exit(code ?? 0);
});
process.on("SIGTERM", () => child.kill("SIGTERM"));
process.on("SIGINT", () => child.kill("SIGINT"));
