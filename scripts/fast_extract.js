#!/usr/bin/env node
/**
 * 快速图片解析器 — 模板列位置 + 并行 OCR + 总结行校验
 *
 * 已识别的供应商模板（按图片尺寸匹配重量列中心 x 坐标）：
 *   恒泽       907×528 / 907×1056   → 8列
 *   多利宝     785×519              → 10列
 *   特宁恒盛   888×528              → 10列
 *   荣兴泰     888×566              → 11列
 * 未知尺寸 → 通用网格检测回退。
 *
 * 用法： node scripts/fast_extract.js <图片> [--json]
 */
const sharp = require('sharp');
const Tesseract = require('tesseract.js');
const path = require('path');
const fs = require('fs');

// ============ 模板注册表 ============
// key: "宽x高"（按宽分组，高取整到 528 的倍数）
function templateCols(W, H) {
  if (W >= 900 && W <= 912) return [390, 431, 471, 512, 553, 594, 635, 675]; // 恒泽 907
  if (W >= 780 && W <= 790) return [212, 244, 276, 308, 340, 373, 405, 438, 470, 502]; // 多利宝 785
  if (W >= 884 && W <= 890 && H <= 535) return [352, 385, 418, 452, 485, 518, 551, 585, 618, 650]; // 特宁恒盛 888×528
  if (W >= 884 && W <= 890 && H >= 560) return [284, 322, 359, 396, 433, 470, 507, 544, 581, 618, 653]; // 荣兴泰 888×566
  return null; // 未知
}

// ============ 工具 ============
function otsu(buf) {
  const hist = new Uint32Array(256);
  for (let i = 0; i < buf.length; i++) hist[buf[i]]++;
  let sum = 0;
  for (let t = 0; t < 256; t++) sum += t * hist[t];
  let wB = 0, sumB = 0, maxV = 0, best = 128, tot = buf.length;
  for (let t = 0; t < 256; t++) {
    wB += hist[t]; if (wB === 0) continue;
    const wF = tot - wB; if (wF === 0) break;
    sumB += t * hist[t];
    const v = wB * wF * (sumB / wB - (sum - sumB) / wF) ** 2;
    if (v > maxV) { maxV = v; best = t; }
  }
  const bw = Buffer.alloc(buf.length);
  for (let i = 0; i < buf.length; i++) bw[i] = buf[i] > best ? 255 : 0;
  return bw;
}

function detectLines(arr, threshold, minGap) {
  const lines = [];
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > threshold) {
      if (lines.length === 0 || i - lines[lines.length - 1] > minGap) lines.push(i);
      else lines[lines.length - 1] = i;
    }
  }
  return lines;
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
  return { raw: data.text.trim(), val: (val !== null && !isNaN(val)) ? val : null };
}

// ============ 主流程 ============
async function fastExtract(imagePath) {
  const img = sharp(imagePath);
  const meta = await img.metadata();
  const W = meta.width, H = meta.height;

  // 1. 重量列
  let weightCols = templateCols(W, H).map(cx => ({ cx }));
  if (!weightCols.length) {
    // 通用回退：检测窄列
    const gray = await img.clone().grayscale().raw().toBuffer();
    const vProj = new Array(W).fill(0);
    for (let x = 0; x < W; x++) { let d = 0; for (let y = 0; y < H; y++) if (gray[y * W + x] < 120) d++; vProj[x] = d; }
    const vLines = detectLines(vProj, Math.round(H * 0.3), 2);
    const cols = [];
    for (let i = 0; i < vLines.length - 1; i++) {
      const w = vLines[i + 1] - vLines[i];
      if (w >= 28 && w <= 48) cols.push(Math.round((vLines[i] + vLines[i + 1]) / 2));
    }
    weightCols = cols.map(cx => ({ cx }));
  }

  // 2. 数据行（水平线检测）
  const gray = await img.clone().grayscale().raw().toBuffer();
  const hProj = new Array(H).fill(0);
  for (let y = 0; y < H; y++) { let d = 0; for (let x = 0; x < W; x++) if (gray[y * W + x] < 120) d++; hProj[y] = d; }
  const hLines = detectLines(hProj, Math.round(W * 0.35), 3);
  const rows = [];
  for (let i = 0; i < hLines.length - 1; i++) {
    const h = hLines[i + 1] - hLines[i];
    if (h >= 18 && h <= 45) rows.push({ cy: Math.round((hLines[i] + hLines[i + 1]) / 2), h });
  }

  // 3. 并行 OCR
  const WORKERS = 6;
  const workers = [];
  for (let i = 0; i < WORKERS; i++) {
    const w = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await w.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789.' });
    workers.push(w);
  }
  const cells = [];
  for (const r of rows) for (const c of weightCols) cells.push({ r, c });
  const results = await Promise.all(cells.map((cell, idx) =>
    cellOCR(img, cell.c.cx, cell.r.cy, 32, cell.r.h - 2, workers[idx % WORKERS]).then(res => ({ ...res, r: cell.r.cy, c: cell.c.cx }))
  ));
  for (const w of workers) await w.terminate();

  // 4. 按行过滤 + 提取重量
  const rowMap = new Map();
  for (const r of results) {
    if (r.val === null || r.val < 15 || r.val > 60) continue;
    if (!rowMap.has(r.r)) rowMap.set(r.r, []);
    rowMap.get(r.r).push(r.val);
  }
  const weights = [];
  for (const [ry, vals] of rowMap) {
    if (vals.length >= 2) { // 数据行至少2个值，过滤空行/表头/汇总行
      weights.push(...vals.map(v => Math.round(v * 10) / 10));
    }
  }

  // 5. 全页 OCR 总结行
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

  // 截断到总结匹数：去掉汇总行/表头噪音（数据行在顶部，按行序在前）
  if (summary.pieces && weights.length > summary.pieces) {
    weights.length = summary.pieces;
  }

  const nameMatch = text.match(/(?:牛奶丝|罗纹|四面弹|汗布|摇粒绒|德绒|卫衣布|螺纹)[^\n|]{0,10}/);
  const products = [{
    name: nameMatch ? nameMatch[0].trim() : undefined,
    spec: undefined,
    color: undefined,
  }];

  return { summary, products, weights, rawSum: weights.reduce((s, v) => s + v, 0), cells: results };
}

// ============ CLI ============
if (require.main === module) {
  (async () => {
    const args = process.argv.slice(2);
    const imgArg = args.find(a => !a.startsWith('--'));
    if (!imgArg) { console.error('用法: node scripts/fast_extract.js <图片> [--json]'); process.exit(1); }
    const r = await fastExtract(path.resolve(imgArg));
    console.log('\n=== 解析结果 ===');
    console.log('总结行:', (r.summary.pieces || '?') + '匹 ' + (r.summary.kg || '?') + '公斤 金额' + (r.summary.amount || '-'));
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
