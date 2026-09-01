// deck.html → 13 张 3200×1800 高清页图（供 make-pptx.py 组装 .pptx）
// 用法：node pitch/make-pptx.cjs   （产出 /tmp/pw/pptx-slides/slide-NN.png + titles.json）
// 截的是"内容页"——导航条与进度条在截图前隐藏。
const fs = require('fs');
const { chromium } = require('playwright-core');

(async () => {
  const OUT = '/tmp/pw/pptx-slides';
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });

  const b = await chromium.launch({
    executablePath: process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1234/chrome-linux/headless_shell',
    args: ['--no-sandbox'],
  });
  const p = await b.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 });
  await p.goto('file://' + __dirname + '/deck.html');
  await p.evaluate(() => document.fonts.ready);
  // 隐藏查看器 chrome：导航条、进度条
  await p.addStyleTag({ content: '.nav,.progress{display:none !important}' });
  await p.waitForTimeout(400);

  const total = await p.evaluate(() => document.querySelectorAll('.slide').length);
  const titles = [];
  for (let i = 1; i <= total; i++) {
    await p.evaluate((n) => {
      const btn = document.getElementById('nextBtn');
      // 逐页前进，保持与真人翻页一致（body 暗色同步等副作用都走 showSlide 路径）
      while (parseInt(document.getElementById('current').textContent, 10) !== n) btn.click();
    }, i);
    await p.waitForTimeout(1200); // anim 最长 .24s 延迟 + .6s 时长，留足余量
    const title = await p.evaluate(() => {
      const s = document.querySelector('.slide.active');
      const h = s.querySelector('h1,h2,h3');
      return h ? h.innerText.replace(/\s+/g, ' ').trim() : '';
    });
    titles.push(title);
    await p.screenshot({ path: `${OUT}/slide-${String(i).padStart(2, '0')}.png` });
    console.log(`slide ${i}/${total}: ${title}`);
  }
  fs.writeFileSync(OUT + '/titles.json', JSON.stringify(titles, null, 2));
  await b.close();
})();
