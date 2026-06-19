#!/usr/bin/env node
/**
 * 图片解析器 — 逐格OCR
 *
 * 规则：
 *   - 每个格子独立OCR（400×160px目标尺寸，lanczos3放大）
 *   - Otsu二值化
 *   - 只提取数字和点
 *   - 总结行独立提取（总匹数、总公斤、单价）
 *   - 不做任何AI修正
 */
const sharp = require('sharp');
const Tesseract = require('tesseract.js');
const path = require('path');
const fs = require('fs');

const ORIG_W = 994, PAGE_H = 528;
const ROW_Y = [192, 214, 237, 260, 282, 305, 328, 350, 373, 396];
const COL_X = [369, 410, 451, 491, 532, 573, 614, 655, 695, 736];

async function parseInvoice(imagePath) {
  const img = sharp(imagePath);
  const meta = await img.metadata();
  const s = meta.width / ORIG_W;
  const pageCount = Math.round(meta.height / (PAGE_H * s));
  console.log(`Image: ${meta.width}x${meta.height}  pages=${pageCount}`);

  // ===== 总结行 =====
  console.log('\n--- 总结行 ---');
  const big = await img.clone()
    .resize(meta.width * 4, meta.height * 4, { kernel: 'lanczos3' })
    .png().toBuffer();

  const ws = await Tesseract.createWorker('chi_sim', 1, { logger: () => {} });
  await ws.setParameters({ tessedit_pageseg_mode: '4' });
  const { data: fullText } = await ws.recognize(big);
  await ws.terminate();

  // 总行模式: OCR可能读成"数要"或"数量"
  const totalLine = fullText.text.match(/总\s*[数数量]\s*[量要]\s*[:：\s]*(\d+)\s*匹\s*([\d.\s]+?)\s*公斤/);
  const amountMatch = fullText.text.match(/总\s*[金全]\s*[额领]\s*[:：\s]*([\d,.\s]+?)(?:\s*本\s*单|$)/);
  const totalPieces = totalLine ? parseInt(totalLine[1]) : null;
  const totalKg = totalLine ? parseFloat(totalLine[2].replace(/\s/g, '')) : null;
  const totalAmount = amountMatch ? parseFloat(amountMatch[1].replace(/[\s,]/g, '')) : null;
  const unitPrice = (totalKg && totalAmount) ? Math.round(totalAmount / totalKg * 100) / 100 : null;
  console.log(`  ${totalPieces}匹 ${totalKg}公斤 ${totalAmount}元 单价${unitPrice}元`);

  // ===== 逐格OCR =====
  console.log('\n--- 逐格OCR ---');
  const cellW = Math.round(41 * s);
  const cellH = Math.round(22 * s);

  const workers = [];
  for (let i = 0; i < 4; i++) {
    const w = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await w.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789.' });
    workers.push(w);
  }

  const allCells = [];
  let done = 0;
  const total = pageCount * 100;

  await Promise.all([...Array(total)].map(async (_, idx) => {
    const p = Math.floor(idx / 100);
    const ri = Math.floor((idx % 100) / 10);
    const ci = idx % 10;
    const x = Math.round(COL_X[ci] * s);
    const y = Math.round((p * PAGE_H + ROW_Y[ri]) * s);
    if (x + cellW > meta.width || y + cellH > meta.height) return;

    const buf = await img.clone()
      .extract({ left: x, top: y, width: cellW, height: cellH })
      .resize(400, 160, { fit: 'fill', kernel: 'lanczos3' })
      .grayscale().normalize().sharpen(0.7).raw()
      .toBuffer();

    // Otsu
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

    const png = await sharp(bw, { raw: { width: 400, height: 160, channels: 1 } }).png().toBuffer();
    const { data } = await workers[idx % 4].recognize(png);

    done++;
    if (done % 100 === 0) process.stdout.write(`\r  ${done}/${total}`);

    const cleaned = data.text.replace(/[^0-9.]/g, '').trim();
    if (cleaned) {
      const v = parseFloat(cleaned);
      if (!isNaN(v)) allCells.push({ page: p + 1, row: ri + 7, col: ci + 1, value: v, raw: data.text.trim() });
    }
  }));

  if (done === total) process.stdout.write(`\r  ${done}/${total} done\n`);
  for (const w of workers) await w.terminate();

  // ===== 过滤 & 缩放 =====
  // 按行分组，只保留≥5个有效值（15-60kg）的行（排除表尾噪音行）
  const rowMap = new Map();
  for (const c of allCells) {
    if (c.value < 15 || c.value > 60) continue;
    const k = `P${c.page}R${c.row}`;
    if (!rowMap.has(k)) rowMap.set(k, { page: c.page, row: c.row, cells: [] });
    rowMap.get(k).cells.push(c);
  }

  const validRows = [...rowMap.values()].filter(r => r.cells.length >= 5)
    .sort((a, b) => a.page - b.page || a.row - b.row);

  // 展示
  let rawWeights = [];
  for (const row of validRows) {
    row.cells.sort((a, b) => a.col - b.col);
    const vals = row.cells.map(c => Math.round(c.value * 10) / 10);
    const sum = Math.round(vals.reduce((s, v) => s + v, 0) * 10) / 10;
    console.log(`  ${vals.length}个 [${vals.join(', ')}] = ${sum}`);
    rawWeights.push(...vals);
  }

  const rawSum = Math.round(rawWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  console.log(`\n过滤后: ${rawWeights.length}匹 ${rawSum}公斤`);

  // 按总结行缩放（规则：逐格分布 × 图片总公斤/逐格总公斤）
  let finalWeights = rawWeights;
  if (totalKg && Math.abs(totalKg - rawSum) > 0.05) {
    const factor = totalKg / rawSum;
    finalWeights = rawWeights.map(w => Math.round(w * factor * 10) / 10);
    // 修复四舍五入误差：取捨入误差最大的权重±0.1
    let diff = Math.round((totalKg - finalWeights.reduce((s, w) => s + w, 0)) * 100) / 100;
    const nudge = diff > 0 ? 0.1 : -0.1;
    const errors = rawWeights.map((w, i) => ({ i, e: Math.abs(w * factor - finalWeights[i]) }))
      .sort((a, b) => b.e - a.e);
    for (let i = 0; i < errors.length && Math.abs(diff) >= 0.05; i++) {
      const nv = Math.round((finalWeights[errors[i].i] + nudge) * 10) / 10;
      if (nv >= 15 && nv <= 60) {
        finalWeights[errors[i].i] = nv;
        diff = Math.round((diff - nudge) * 100) / 100;
      }
    }
  }

  const finalSum = Math.round(finalWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  if (totalKg) console.log(`缩放后: ${finalWeights.length}匹 ${finalSum}公斤 (目标${totalKg}，差异${Math.round(Math.abs(totalKg - finalSum) * 100) / 100})`);
  else console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤`);

  return {
    summary: { pieces: totalPieces, kg: totalKg, amount: totalAmount, price: unitPrice },
    cells: allCells,
    weights: finalWeights,
  };
}

if (require.main === module) {
  (async () => {
    const imgArg = process.argv[2];
    if (!imgArg) { process.exit(1); }
    const result = await parseInvoice(path.resolve(imgArg));
    fs.writeFileSync(path.join(__dirname, '..', 'temp', 'parsed_data.json'), JSON.stringify(result, null, 2));
    console.log(`已保存`);
  })();
}

module.exports = { parseInvoice };
