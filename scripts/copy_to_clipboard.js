const { execSync } = require('child_process');
const sharp = require('sharp');
const path = require('path');
const fs = require('fs');

async function combineAndCopy(xlsxPath, qrPaths) {
  const xlsxAbs = path.resolve(xlsxPath);
  const tempDir = path.resolve('temp');
  if (!fs.existsSync(tempDir)) fs.mkdirSync(tempDir, { recursive: true });

  // 文字已由 generate() 放入剪贴板
  console.log('📊 Excel A1:U21 截图中（文字已在剪贴板，先Ctrl+V发文字）...');

  // Step 1: Excel截图
  const excelPng = path.join(tempDir, 'excel_screenshot.png');
  const psScript = path.resolve(__dirname, 'excel_screenshot.ps1');
  try {
    execSync(`powershell -File "${psScript}" "${xlsxAbs}" "${excelPng}"`, { stdio: 'pipe', timeout: 30000 });
  } catch (e) {
    await htmlScreenshot(xlsxPath, excelPng);
  }

  if (!fs.existsSync(excelPng)) { console.log('Screenshot failed'); return; }
  const excelMeta = await sharp(excelPng).metadata();
  console.log('  Excel:', excelMeta.width, 'x', excelMeta.height);

  // Step 2: 合并收款图
  const qrImgs = [];
  for (const qrPath of qrPaths) {
    if (!fs.existsSync(qrPath)) continue;
    const maxW = Math.round(excelMeta.width * 0.7);
    const resized = await sharp(qrPath).resize(maxW, null, { fit: 'inside', withoutEnlargement: true }).png().toBuffer();
    qrImgs.push({ buf: resized, meta: await sharp(resized).metadata() });
  }

  if (qrImgs.length === 0) return;

  const gap = 10;
  const totalQrH = qrImgs.reduce((s, q) => s + q.meta.height + gap, 0);
  const totalH = excelMeta.height + totalQrH + gap;

  const composites = [{ input: excelPng, top: 0, left: 0 }];
  let y = excelMeta.height + gap;
  for (const qr of qrImgs) {
    composites.push({ input: qr.buf, top: y, left: Math.round((excelMeta.width - qr.meta.width) / 2) });
    y += qr.meta.height + gap;
  }

  const combined = await sharp({
    create: { width: excelMeta.width, height: totalH, channels: 4, background: { r: 255, g: 255, b: 255, alpha: 1 } }
  })
  .composite(composites)
  .png()
  .toBuffer();

  const combinedPath = path.resolve('temp/combined.png');
  fs.writeFileSync(combinedPath, combined);

  // Step 3: 图片 → 剪贴板
  const ps = `Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; $img = [System.Drawing.Image]::FromFile('${combinedPath.replace(/\\/g, '\\\\')}'); [System.Windows.Forms.Clipboard]::SetImage($img); $img.Dispose()`;
  execSync(`powershell -Command "${ps}"`, { stdio: 'ignore' });
  console.log('📋 合并图 ' + excelMeta.width + 'x' + totalH + ' 已复制到剪贴板 → Ctrl+V 粘贴');
}

async function htmlScreenshot(xlsxPath, outPath) {
  const puppeteer = require('puppeteer');
  const dir = path.dirname(xlsxPath);
  const base = path.basename(xlsxPath, '.xlsx');
  const htmlPath = path.join(dir, base.replace('销售单', '预览') + '.html');
  if (!fs.existsSync(htmlPath)) return;
  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewport({ width: 1200, height: 800 });
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle0' });
  const bodyBox = await page.evaluate(() => { const r = document.body.getBoundingClientRect(); return { width: Math.ceil(r.width), height: Math.ceil(r.height) }; });
  const png = await page.screenshot({ clip: { x: 0, y: 0, width: bodyBox.width + 20, height: bodyBox.height + 20 } });
  await browser.close();
  fs.writeFileSync(outPath, png);
}

const [,, xlsx, ...qrs] = process.argv;
combineAndCopy(xlsx, qrs.filter(f => fs.existsSync(f)));
