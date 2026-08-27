// deck.html → 单文件内联版（图片 base64 进 HTML）
// 用法：node pitch/inline.cjs（任意 cwd 均可）
// 产出两份同内容文件：
//   pitch/deck-standalone.html        —— 对外直发的单文件交付物
//   infra/pitch-site/intro/index.html —— /intro/ 线上部署真源（Caddy 直读）
// 注意：deck.html 是唯一编辑源；改完必须跑本脚本，两份产物才会更新。
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, 'deck.html');
const SHOTS = path.join(__dirname, 'shots');
const OUTS = [
  path.join(__dirname, 'deck-standalone.html'),
  path.join(__dirname, '..', 'infra', 'pitch-site', 'intro', 'index.html'),
];

let html = fs.readFileSync(SRC, 'utf8');

// 图片内联
for (const f of fs.readdirSync(SHOTS).filter((f) => f.endsWith('.png'))) {
  const b64 = fs.readFileSync(path.join(SHOTS, f)).toString('base64');
  const before = html.length;
  html = html.split(`shots/${f}`).join(`data:image/png;base64,${b64}`);
  console.log(`${f}: inlined, +${html.length - before} bytes`);
}

for (const out of OUTS) {
  fs.writeFileSync(out, html);
  console.log(`${out}: ${(fs.statSync(out).size / 1024 / 1024).toFixed(2)} MB`);
}
