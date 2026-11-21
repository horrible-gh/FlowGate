/* Extract comments using the project's existing TypeScript and Vue parsers. */
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const clientRequire = createRequire(path.resolve(__dirname, '../client_gui/package.json'));
const ts = clientRequire('typescript');
const { parse: parseVue } = clientRequire('@vue/compiler-sfc');
const { baseParse } = clientRequire('@vue/compiler-dom');

function javascript(name, text, offset, comments, literals) {
  const kind = /\.[jt]sx$/.test(name) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const tree = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, kind);
  const protectedRanges = [];
  function visit(node) {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      literals.push(node.text);
      protectedRanges.push([node.getStart(tree), node.end]);
    } else if (ts.isRegularExpressionLiteral(node) || ts.isJsxText(node) ||
               node.kind === ts.SyntaxKind.TemplateHead ||
               node.kind === ts.SyntaxKind.TemplateMiddle || node.kind === ts.SyntaxKind.TemplateTail) {
      protectedRanges.push([node.getStart(tree), node.end]);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  protectedRanges.sort((a, b) => a[0] - b[0]);
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.Standard, text);
  let index = 0;
  while (true) {
    const token = scanner.scan();
    if (token === ts.SyntaxKind.EndOfFileToken) break;
    const start = scanner.getTokenPos();
    while (index < protectedRanges.length && protectedRanges[index][1] <= start) index++;
    if (index < protectedRanges.length && protectedRanges[index][0] <= start) {
      scanner.setTextPos(protectedRanges[index][1]);
      continue;
    }
    if (token === ts.SyntaxKind.SingleLineCommentTrivia || token === ts.SyntaxKind.MultiLineCommentTrivia) {
      const line = offset + tree.getLineAndCharacterOfPosition(start).line + 1;
      scanner.getTokenText().split(/\r?\n/).forEach((value, n) => comments.push([line + n, value]));
    }
  }
}

function extract(name, text) {
  const comments = [], literals = [];
  if (!name.endsWith('.vue')) {
    javascript(name, text, 0, comments, literals);
    return [comments, literals];
  }
  const { descriptor, errors } = parseVue(text, { filename: name });
  if (errors.length) throw new Error(`${name}: ${errors.join('; ')}`);
  for (const block of [descriptor.script, descriptor.scriptSetup]) {
    if (block) javascript(name + (block.lang === 'tsx' ? '.tsx' : '.ts'), block.content,
      block.loc.start.line - 1, comments, literals);
  }
  if (descriptor.template) {
    const block = descriptor.template;
    const tree = baseParse(block.content, { comments: true });
    function walk(node) {
      if (node.type === 3) {
        const line = block.loc.start.line + node.loc.start.line - 1;
        node.loc.source.split(/\r?\n/).forEach((value, n) => comments.push([line + n, value]));
      } else if (node.type === 2) {
        literals.push(node.content.trim());
      } else if (node.type === 5) {
        javascript(name + '.ts', node.content.content,
          block.loc.start.line + node.content.loc.start.line - 2, comments, literals);
      }
      for (const prop of node.props || []) {
        if (prop.type === 6 && prop.value) literals.push(prop.value.content);
        if (prop.type === 7 && prop.exp) javascript(name + '.ts', prop.exp.content,
          block.loc.start.line + prop.exp.loc.start.line - 2, comments, literals);
      }
      for (const child of node.children || []) walk(child);
    }
    walk(tree);
  }
  for (const block of descriptor.styles) {
    // CSS has block comments and quoted strings, but no line comments.
    const tokens = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\*[\s\S]*?\*\//g;
    for (const match of block.content.matchAll(tokens)) {
      if (!match[0].startsWith('/*')) continue;
      const line = block.loc.start.line + block.content.slice(0, match.index).split('\n').length - 1;
      match[0].split(/\r?\n/).forEach((value, n) => comments.push([line + n, value]));
    }
  }
  return [comments, literals];
}

function astShape(name, text) {
  const kind = /\.[jt]sx$/.test(name) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const tree = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, kind);
  // Compare diagnostics too: error recovery can hide malformed comment edits.
  // Existing syntax errors remain visible without treating shifted offsets as changes.
  const shape = [[tree.parseDiagnostics.map(error => [error.code,
    ts.flattenDiagnosticMessageText(error.messageText, ' ')])]];
  function visit(node) {
    const value = node.kind !== ts.SyntaxKind.SourceFile && typeof node.text === 'string'
      ? [node.text, node.rawText || null] : null;
    const children = [];
    ts.forEachChild(node, child => { children.push(child); });
    shape.push([node.kind, value, children.length]);
    children.forEach(visit);
  }
  visit(tree);
  return shape;
}

function cssWithoutComments(text) {
  return text.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\*[\s\S]*?\*\//g,
    token => token.startsWith('/*') ? '' : token);
}

function runtimeShape(name, text) {
  if (!name.endsWith('.vue')) return astShape(name, text);
  const { descriptor, errors } = parseVue(text, { filename: name });
  if (errors.length) throw new Error(`${name}: ${errors.join('; ')}`);
  const blockShape = (block, mode) => !block ? null : [block.attrs,
    mode === 'script' ? astShape(name + (block.lang === 'tsx' ? '.tsx' : '.ts'), block.content) :
    mode === 'style' ? cssWithoutComments(block.content) :
    block.content.replace(/<!--[\s\S]*?-->/g, '')];
  return [blockShape(descriptor.script, 'script'), blockShape(descriptor.scriptSetup, 'script'),
    blockShape(descriptor.template, 'template'), descriptor.styles.map(block => blockShape(block, 'style')),
    descriptor.customBlocks.map(block => [block.type, block.attrs, block.content])];
}

function verifyChanges() {
  const { execFileSync } = require('node:child_process');
  const root = path.resolve(__dirname, '..');
  const git = args => execFileSync('git', args, { cwd: root, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  const files = git(['diff', '--name-only', '--diff-filter=M', 'HEAD', '--', 'client_gui'])
    .trim().split(/\r?\n/).filter(name => /\.(?:[cm]?js|jsx|tsx?|vue)$/.test(name));
  const failures = [];
  for (const name of files) {
    const before = git(['show', `HEAD:${name}`]).replace(/\r\n/g, '\n');
    const after = fs.readFileSync(path.join(root, name), 'utf8').replace(/\r\n/g, '\n');
    if (JSON.stringify(runtimeShape(name, before)) !== JSON.stringify(runtimeShape(name, after))) failures.push(name);
  }
  process.stdout.write(JSON.stringify({ checked: files.length, changed_runtime: failures }) + '\n');
  process.exitCode = failures.length ? 1 : 0;
}

if (process.argv.includes('--verify-changes')) {
  verifyChanges();
} else {
  const sources = JSON.parse(fs.readFileSync(0, 'utf8'));
  const result = Object.fromEntries(Object.entries(sources).map(([name, text]) => [name, extract(name, text)]));
  process.stdout.write(JSON.stringify(result));
}
