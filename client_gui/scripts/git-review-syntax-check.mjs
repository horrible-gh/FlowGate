// Real-parser syntax check for the git merge review pre-commit gate
// (L0007 §2.7). Invoked by server/modules/flow_gate/services/git_service.py
// as a subprocess: one JSON object on stdin `{ "ext": "ts", "content": "..." }`,
// one JSON object on stdout `{ "ok": true }` or
// `{ "ok": false, "line": 12, "message": "..." }`. Never touches disk or the
// network — every check parses the given `content` string only.
//
// Uses the SAME compiler packages the client build already depends on
// (typescript, @vue/compiler-sfc/@vue/compiler-dom, postcss) plus
// @babel/parser (a transitive dependency of @vue/compiler-sfc already present
// in node_modules) for plain ECMAScript — no new dependency is added to
// package.json, this only wires up what is already installed.
import ts from "typescript";
import { parse as parseSfc, compileTemplate } from "@vue/compiler-sfc";
import postcss from "postcss";
import { parse as parseBabel } from "@babel/parser";

const JS_LIKE = new Set(["js", "mjs", "cjs", "jsx"]);
const TS_LIKE = new Set(["ts", "mts", "cts", "tsx"]);
const CSS_LIKE = new Set(["css", "scss"]);

function ok() {
  return { ok: true };
}

function fail(line, message) {
  return { ok: false, line: line ?? null, message: String(message) };
}

function checkEcmaScript(content, jsx) {
  try {
    parseBabel(content, {
      sourceType: "unambiguous",
      allowReturnOutsideFunction: true,
      allowImportExportEverywhere: true,
      allowAwaitOutsideFunction: true,
      plugins: jsx ? ["jsx"] : [],
    });
    return ok();
  } catch (err) {
    return fail(err?.loc?.line, err?.message || String(err));
  }
}

function checkTypeScript(content, tsx) {
  const compilerOptions = {
    target: ts.ScriptTarget.Latest,
    module: ts.ModuleKind.ESNext,
    // no-emit syntactic check only: transpileModule never resolves other
    // modules or performs full-program type checking, so this can only ever
    // surface syntax errors — exactly the "no-emit syntactic check" L0007
    // §2.7 asks for, without requiring this process to load the whole
    // project's tsconfig/module graph.
  };
  // `jsx` must be omitted (not set to JsxEmit.None) for plain .ts content —
  // transpileModule rejects JsxEmit.None itself with a bogus "--jsx option
  // must be preserve/react/..." diagnostic (verified against this repo's
  // pinned typescript 5.9.3).
  if (tsx) compilerOptions.jsx = ts.JsxEmit.Preserve;
  const result = ts.transpileModule(content, {
    compilerOptions,
    reportDiagnostics: true,
    fileName: tsx ? "review-check.tsx" : "review-check.ts",
  });
  const diags = result.diagnostics || [];
  if (diags.length === 0) return ok();
  const diag = diags[0];
  let line = null;
  if (diag.file && typeof diag.start === "number") {
    line = diag.file.getLineAndCharacterOfPosition(diag.start).line + 1;
  }
  return fail(line, ts.flattenDiagnosticMessageText(diag.messageText, "\n"));
}

function checkCss(content) {
  try {
    postcss.parse(content);
    return ok();
  } catch (err) {
    if (err && err.name === "CssSyntaxError") {
      return fail(err.line, err.reason || err.message);
    }
    return fail(null, err?.message || String(err));
  }
}

function checkVue(content) {
  const { descriptor, errors } = parseSfc(content, { filename: "file.vue" });
  if (errors && errors.length) {
    const e = errors[0];
    return fail(e?.loc?.start?.line, e.message || String(e));
  }
  if (descriptor.template && !descriptor.template.src) {
    const tpl = compileTemplate({
      source: descriptor.template.content,
      filename: "file.vue",
      id: "review-check",
    });
    if (tpl.errors && tpl.errors.length) {
      const e = tpl.errors[0];
      const loc = typeof e === "object" ? e.loc : null;
      return fail(loc?.start?.line, (e && e.message) || String(e));
    }
  }
  // An SFC may legally carry BOTH a `<script setup>` and an ordinary `<script>`
  // block (e.g. `<script>export default { inheritAttrs: false }</script>` next
  // to `<script setup>`) — Vue's own compiler merges them at build time. Only
  // checking one via `scriptSetup || script` let a syntax error in whichever
  // block lost that `||` reach approval/commit unnoticed; both are validated
  // independently here.
  for (const scriptBlock of [descriptor.scriptSetup, descriptor.script]) {
    if (!scriptBlock || scriptBlock.src) continue;
    const lang = (scriptBlock.lang || "js").toLowerCase();
    const result = TS_LIKE.has(lang) || lang === "ts" || lang === "tsx"
      ? checkTypeScript(scriptBlock.content, lang === "tsx")
      : checkEcmaScript(scriptBlock.content, lang === "jsx");
    if (!result.ok) return result;
  }
  for (const style of descriptor.styles || []) {
    if (style.src) continue;
    const result = checkCss(style.content);
    if (!result.ok) return result;
  }
  return ok();
}

function check(ext, content) {
  const e = (ext || "").toLowerCase();
  if (TS_LIKE.has(e)) return checkTypeScript(content, e === "tsx");
  if (JS_LIKE.has(e)) return checkEcmaScript(content, e === "jsx");
  if (CSS_LIKE.has(e)) return checkCss(content);
  if (e === "vue") return checkVue(content);
  return fail(null, `unsupported extension for this checker: ${ext}`);
}

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  raw += chunk;
});
process.stdin.on("end", () => {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    process.stdout.write(JSON.stringify(fail(null, `invalid stdin JSON: ${err.message}`)));
    process.exitCode = 1;
    return;
  }
  let result;
  try {
    result = check(payload.ext, payload.content ?? "");
  } catch (err) {
    result = fail(null, `internal checker error: ${err?.message || err}`);
  }
  process.stdout.write(JSON.stringify(result));
});
