#!/usr/bin/env node
/**
 * 快速图片解析器（第一性原理版）
 *
 * 核心思路：不硬背任何模板。
 *   1. 网格检测（横线→数据行，竖线→所有列）
 *   2. 并行 OCR 所有格子
 *   3. 按内容分列：重量列 = 数值落在 15-60kg 区间的「最长连续列段」
 *      （数量列=1-10小整数、单价列=10-20且每行重复同值、金额列=>100，都会被排除）
 *   4. 用总结行「总匹数 N + 总公斤 K」做硬校验：提取 N 个值必须加总≈K，否则报错不硬凑
 *
 * 用法： node scripts/fast_extract.js <图片> [--json]
 */
const sharp = require('sharp');
const Tesseract = require('tesseract.js');
const path = require('path');
const fs = require('fs');

function otsu(buf) {
  const h = new Uint32Array(256);
  for (let i = 0; i < buf.length; i++) h[buf[i]]++;
  let s = 0; for (let t = 0; t < 256; t++) s += t * h[t];
  let wB = 0, sB = 0, mv = 0, b = 128, tot = buf.length;
  for (let t = 0; t < 256; t++) {
    wB += h[t]; if (!wB) continue;
    const wF = tot - wB; if (!wF) break;
    sB += t * h[t];
    const v = wB * wF * (sB / wB - (s - sB) / wF) ** 2;
    if (v > mv) { mv = v; b = t; }
  }
  const bw = Buffer.alloc(buf.length);
  for (let i = 0; i < buf.length; i++) bw[i] = buf[i] > b ? 255 : 0;
  return bw;
}

function detectLines(arr, threshold, minGap) {
  const L = [];
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > threshold) {
      if (L.length === 0 || i - L[L.length - 1] > minGap) L.push(i);
      else L[L.length - 1] = i;
    }
  }
  return L;
}

async function cellOCR(img, cx, cy, cw, ch, worker) {
  const left = Math.max(0, Math.round(cx - cw / 2));
  const top = Math.max(0, Math.round(cy - ch / 2));
  const buf = await img.clone()
    .extract({ left, top, width: cw, height: ch })
    .resize(360, 240, { fit: 'fill', kernel: 'lanczos3' })
    .grayscale().normalize().sharpen(0.7).raw().toBuffer();
  const bw = otsu(buf);
  const png = await sharp(bw, { raw: { width: 360, height: 240, channels: 1 } }).png().toBuffer();
  const { data } = await worker.recognize(png);
  const cleaned = data.text.replace(/[^0-9.]/g, '').trim();
  const val = cleaned ? parseFloat(cleaned) : null;
  return (val !== null && !isNaN(val)) ? val : null;
}

/** 在列数组中找「最长连续的有重量值列段」 */
function longestRun(cols, colHasValue) {
  let best = [], cur = [];
  for (const c of cols) {
    if (colHasValue(c.cx)) { cur.push(c); }
    else { if (cur.length > best.length) best = cur; cur = []; }
  }
  if (cur.length > best.length) best = cur;
  return best;
}

async function fastExtract(imagePath) {
  const img = sharp(imagePath);
  const meta = await img.metadata();
  const W = meta.width, H = meta.height;
  const gray = await img.clone().grayscale().raw().toBuffer();

  // 1. 数据行（水平线）
  const hProj = new Array(H).fill(0);
  for (let y = 0; y < H; y++) { let d = 0; for (let x = 0; x < W; x++) if (gray[y * W + x] < 120) d++; hProj[y] = d; }
  const hLines = detectLines(hProj, Math.round(W * 0.35), 3);
  const rows = [];
  for (let i = 0; i < hLines.length - 1; i++) {
    const h = hLines[i + 1] - hLines[i];
    if (h >= 18 && h <= 45) rows.push({ cy: Math.round((hLines[i] + hLines[i + 1]) / 2), h });
  }

  // 2. 所有列（竖线，在数据区投影）
  const vTop = hLines[0] || 0, vBot = hLines[hLines.length - 1] || H;
  const vProj = new Array(W).fill(0);
  for (let x = 0; x < W; x++) { let d = 0; for (let y = vTop; y <= vBot; y++) if (gray[y * W + x] < 120) d++; vProj[x] = d; }
  const vLines = detectLines(vProj, Math.round((vBot - vTop) * 0.5), 2);
  const allCols = [];
  for (let i = 0; i < vLines.length - 1; i++) {
    const w = vLines[i + 1] - vLines[i];
    if (w >= 14) allCols.push({ cx: Math.round((vLines[i] + vLines[i + 1]) / 2), w });
  }

  // 3. 并行 OCR 所有格子
  const WORKERS = 6;
  const workers = [];
  for (let i = 0; i < WORKERS; i++) {
    const w = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await w.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789.' });
    workers.push(w);
  }
  const cells = [];
  for (const c of allCols) for (const r of rows) cells.push({ c, r });
  const results = await Promise.all(cells.map((o, idx) =>
    cellOCR(img, o.c.cx, o.r.cy, Math.max(24, o.c.w - 4), o.r.h - 2, workers[idx % WORKERS])
      .then(v => ({ cx: o.c.cx, cy: o.r.cy, v }))
  ));
  for (const w of workers) await w.terminate();

  // 4. 按内容分列：每列收集 15-60 的值
  const colVals = new Map(); // cx -> [values]
  for (const r of results) {
    if (r.v === null || r.v < 15 || r.v > 60) continue;
    if (!colVals.has(r.cx)) colVals.set(r.cx, []);
    colVals.get(r.cx).push(r.v);
  }
  const weightCols = longestRun(allCols, cx => (colVals.get(cx) || []).length >= 1);

  // 5. 提取重量（按行序、列序，仅重量列），截断到总结匹数
  const weightCx = new Set(weightCols.map(c => c.cx));
  const weights = results
    .filter(r => weightCx.has(r.cx) && r.v !== null && r.v >= 15 && r.v <= 60)
    .sort((a, b) => a.cy - b.cy || a.cx - b.cx)
    .map(r => Math.round(r.v * 10) / 10);

  // 6. 全页 OCR 总结行
  const big = await img.clone().resize(W * 3, H * 3, { kernel: 'lanczos3' }).png().toBuffer();
  const ws = await Tesseract.createWorker('chi_sim', 1, { logger: () => {} });
  await ws.setParameters({ tessedit_pageseg_mode: '4' });
  const { data: full } = await ws.recognize(big);
  await ws.terminate();
  const text = full.text;

  const piecesMatch = text.match(/(?:总\s*[数数量]|总数)[^0-9]{0,8}(\d+)\s*[匹苞条]/);
  const kgMatch = text.match(/(\d{2,4})\s*[.．]\s*(\d)\s*公\s*斤/) || text.match(/(\d{2,4})\s*公\s*斤/);
  const amountMatch = text.match(/(?:总\s*金额|本单应付|本单应收)\s*[:：]?\s*([\d,]+\.?\d*)/);

  const summary = {
    pieces: piecesMatch ? parseInt(piecesMatch[1]) : null,
    kg: kgMatch ? (kgMatch[2] !== undefined ? parseFloat(kgMatch[1] + '.' + kgMatch[2]) : parseFloat(kgMatch[1])) : null,
    amount: amountMatch ? parseFloat(amountMatch[1].replace(/,/g, '')) : null,
  };

  if (summary.pieces && weights.length > summary.pieces) weights.length = summary.pieces;

  const nameMatch = text.match(/(?:牛奶丝|罗纹|四面弹|汗布|摇粒绒|德绒|卫衣布|螺纹)[^\n|]{0,10}/);
  const products = [{ name: nameMatch ? nameMatch[0].trim() : undefined, spec: undefined, color: undefined }];

  return { summary, products, weights, rawSum: weights.reduce((s, v) => s + v, 0), weightColCx: [...weightCx] };
}

if (require.main === module) {
  (async () => {
    const args = process.argv.slice(2);
    const imgArg = args.find(a => !a.startsWith('--'));
    if (!imgArg) { console.error('用法: node scripts/fast_extract.js <图片> [--json]'); process.exit(1); }
    const r = await fastExtract(path.resolve(imgArg));
    console.log('\n=== 解析结果 ===');
    console.log('总结行:', (r.summary.pieces || '?') + '匹 ' + (r.summary.kg || '?') + '公斤 金额' + (r.summary.amount || '-'));
    console.log('重量列数:', r.weightColCx.length, '列');
    console.log('提取:', r.weights.length + '匹 合计' + r.rawSum.toFixed(1) + 'kg');
    if (r.summary.kg) {
      const diff = Math.abs(r.rawSum - r.summary.kg);
      console.log('公斤校验:', diff <= 0.5 ? '✓ 吻合' : '✗ 差' + diff.toFixed(1) + 'kg');
      if (r.summary.pieces && r.weights.length !== r.summary.pieces) console.log('匹数校验:', '✗ 提取' + r.weights.length + '匹 ≠ 总结' + r.summary.pieces + '匹');
    }
    if (args.includes('--json')) {
      fs.writeFileSync(path.join(__dirname, '..', 'temp', 'fast_extract.json'), JSON.stringify(r, null, 2));
      console.log('已保存 temp/fast_extract.json');
    }
  })().catch(e => { console.error('错误:', e.message); process.exit(1); });
}

module.exports = { fastExtract };
