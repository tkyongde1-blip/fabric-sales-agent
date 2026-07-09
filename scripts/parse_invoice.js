#!/usr/bin/env node
/**
 * 图片解析器 - 支持大利标准/恒泽/红呈三种模板
 *
 * 规则（来自规则.md）：
 *   1. 全页OCR → 4倍放大 → chi_sim → 提取总结行/品名/规格
 *   2. 逐格OCR → 固定坐标→ Otsu二值化 → eng数字模式 → 每格独立识别
 *   3. 噪音过滤 → 每行不足5个15-60kg值丢弃
 *   4. 规格逐格OCR → 幅宽/克重/品名从第一行逐格OCR（同重量方式）
 *   5. 总公斤修正 → 逐格之和 ≠ 总结行 → 按比例缩放
 *
 * 禁止规则：
 *   - 禁止AI修正OCR读错的字段
 *   - 禁止用平均值填充
 *   - 解析不到数据直接报错
 */
const sharp = require('sharp');
const Tesseract = require('tesseract.js');
const path = require('path');
const fs = require('fs');

// ==================== 模板检测 ====================
function detectTemplate(width, height) {
  // 大利标准: 994×528（或994×1056等整页倍数）
  if (width >= 980 && width <= 1000 && height >= 520 && (Math.round(height / 528) * 528 === height || height === 528)) {
    return 'DL_STANDARD';
  }
  // 恒泽: 907×528
  if (width >= 895 && width <= 920 && height >= 520 && height <= 540) {
    return 'HENGZE';
  }
  // 红呈: 898×454（双面板布局，左右各6列）
  if (width >= 880 && width <= 920 && height >= 445 && height <= 465) {
    return 'HONGCHENG';
  }
  return 'UNKNOWN';
}

// ==================== 大利标准模板布局（10列×10行每页）====================
const ORIG_W = 994, PAGE_H = 528;
const ROW_Y = [192, 214, 237, 260, 282, 305, 328, 350, 373, 396];
const COL_X = [369, 410, 451, 491, 532, 573, 614, 655, 695, 736];

// ==================== 红呈模板布局（898×454，左右双面板）====================
// 左面板6列+右面板6列=每行12个重量
// 行位置通过水平投影动态检测
const HC_PANEL_LEFT = { startX: 55, endX: 390, cols: 6 };
const HC_PANEL_RIGHT = { startX: 440, endX: 840, cols: 6 };
const HC_DATA_AREA_Y_START = 160;
const HC_DATA_AREA_Y_END = 420;

// ==================== 工具函数 ====================

/** Otsu二值化 */
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

/** 从图像中检测数据行位置（水平投影法） */
async function detectDataRows(img, meta, startY, endY, threshold = 15) {
  const gray = await img.clone().grayscale().raw().toBuffer();
  const rows = [];
  for (let y = startY; y <= endY; y++) {
    let darkCount = 0;
    for (let x = 0; x < meta.width; x++) {
      if (gray[y * meta.width + x] < 120) darkCount++;
    }
    if (darkCount > threshold) {
      if (rows.length === 0 || y > rows[rows.length - 1].end + 4) {
        rows.push({ start: y, end: y });
      } else {
        rows[rows.length - 1].end = y;
      }
    }
  }
  return rows.map(r => ({ start: r.start, end: r.end, center: Math.floor((r.start + r.end) / 2) }));
}

/** 执行逐格OCR（单格） */
async function cellOCR(img, left, top, width, height) {
  const buf = await img.clone()
    .extract({ left, top, width, height })
    .resize(400, 160, { fit: 'fill', kernel: 'lanczos3' })
    .grayscale().normalize().sharpen(0.7).raw()
    .toBuffer();

  const bw = otsu(buf);
  const png = await sharp(bw, { raw: { width: 400, height: 160, channels: 1 } }).png().toBuffer();

  const ws = await Tesseract.createWorker('eng', 1, { logger: () => {} });
  await ws.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789' });
  const { data } = await ws.recognize(png);
  await ws.terminate();

  const cleaned = data.text.replace(/[^0-9]/g, '').trim();
  return cleaned ? parseInt(cleaned) : null;
}

/** 执行逐格OCR提取带小数重量（如 31.3） */
async function cellOCRWeight(img, left, top, width, height) {
  const buf = await img.clone()
    .extract({ left, top, width, height })
    .resize(400, 160, { fit: 'fill', kernel: 'lanczos3' })
    .grayscale().normalize().sharpen(0.7).raw()
    .toBuffer();

  const bw = otsu(buf);
  const png = await sharp(bw, { raw: { width: 400, height: 160, channels: 1 } }).png().toBuffer();

  const ws = await Tesseract.createWorker('eng', 1, { logger: () => {} });
  await ws.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789. ' });
  const { data } = await ws.recognize(png);
  await ws.terminate();

  const cleaned = data.text.replace(/\s+/g, '');
  const nums = cleaned.match(/\d{2}\.\d/g);
  return nums ? parseFloat(nums[0]) : null;
}

/** 执行逐格OCR处理品名（chi_sim） */
async function cellOCRName(img, left, top, width, height) {
  const buf = await img.clone()
    .extract({ left, top, width, height })
    .resize(700, 160, { fit: 'fill', kernel: 'lanczos3' })
    .grayscale().normalize().sharpen(0.7)
    .png().toBuffer();

  const ws = await Tesseract.createWorker('chi_sim', 1, { logger: () => {} });
  await ws.setParameters({ tessedit_pageseg_mode: '7' });
  const { data } = await ws.recognize(buf);
  await ws.terminate();

  const raw = data.text.trim().replace(/\s+/g, '');
  if (raw.length < 2) return null;
  let best = '', cur = '';
  for (const c of raw) {
    if (/[一-鿿]/.test(c)) { cur += c; if (cur.length > best.length) best = cur; }
    else cur = '';
  }
  return best.length >= 2 ? best : null;
}

/** 全页OCR提取总结行 */
async function extractSummary(img, meta) {
  const big = await img.clone()
    .resize(meta.width * 4, meta.height * 4, { kernel: 'lanczos3' })
    .png().toBuffer();

  const ws = await Tesseract.createWorker('chi_sim', 1, { logger: () => {} });
  await ws.setParameters({ tessedit_pageseg_mode: '4' });
  const { data } = await ws.recognize(big);
  await ws.terminate();

  return data.text;
}

/** 从全页文本提取总结行数据 */
function parseSummary(text) {
  const totalLine = text.match(/总\s*[数数量]\s*[量要重]\s*[:：\s]*(\d+)\s*匹\s*([\d.\s]+?)\s*公\s*斤/);
  const totalLine2 = !totalLine ? text.match(/总[^]*?(\d+)\s*匹\s*([\d.\s]+?)\s*公\s*斤/) : null;
  const amountMatch = text.match(/总\s*[金全]\s*[额领]\s*[:：\s]*([\d,.\s]+?)(?:\s*本\s*单|$)/);
  const amountMatch2 = !amountMatch ? text.match(/总[^]*?金额\s*[:：\s]*([\d,.\s]+?)(?:\s*本\s*单|$)/) : null;

  const totalPieces = totalLine ? parseInt(totalLine[1]) : (totalLine2 ? parseInt(totalLine2[1]) : null);
  const totalAmount = amountMatch ? parseFloat(amountMatch[1].replace(/[\s,]/g, '')) : (amountMatch2 ? parseFloat(amountMatch2[1].replace(/[\s,]/g, '')) : null);
  let kgRaw = totalLine ? totalLine[2].replace(/\s/g, '') : (totalLine2 ? totalLine2[2].replace(/\s/g, '') : null);
  let totalKg = kgRaw ? parseFloat(kgRaw) : null;

  return { text, totalPieces, totalKg, totalAmount };
}

/** 从全页文本提取品名/规格 */
function extractSpecAndName(text) {
  let extractedWidth = null, extractedWeight = null, extractedName = null;

  // 从chi_sim文本提取幅宽/克重（含空格和|分隔符）
  const widthMatch = text.match(/幅\s*宽\s*[:：\s|]*(\d{2,3})/);
  const gramMatch = text.match(/克\s*重\s*[:：\s|l1]*(\d{2,3})/);
  if (widthMatch) extractedWidth = parseInt(widthMatch[1]);
  if (gramMatch) extractedWeight = parseInt(gramMatch[1]);

  // 品名提取（chi_sim识别结果常有空格）
  const nameMatch = text.match(/品\s*名\s*[:："”「」\s|]*([^\d|]{4,30}?)(?:\s*[|]\s*|$)/);
  if (nameMatch) {
    const raw = nameMatch[1].replace(/[\s"」」]/g, '');
    let best = '', cur = '';
    for (const c of raw) {
      if (/[一-鿿]/.test(c)) { cur += c; if (cur.length > best.length) best = cur; }
      else cur = '';
    }
    extractedName = best.length >= 2 ? best : null;
  }

  return { extractedWidth, extractedWeight, extractedName };
}

// ==================== 大利标准模板解析 ====================
async function parseDLStandard(img, meta) {
  const s = meta.width / ORIG_W;
  const pageCount = Math.round(meta.height / (PAGE_H * s)) || 1;
  console.log(`大利标准模板: ${meta.width}x${meta.height}  ${pageCount}页`);

  // Step 1: 全页OCR → 总结行
  console.log('\n--- Step 1: 总结行提取 ---');
  const text = await extractSummary(img, meta);
  const { totalPieces, totalKg, totalAmount } = parseSummary(text);
  const unitPrice = (totalKg && totalAmount) ? Math.round(totalAmount / totalKg * 100) / 100 : null;
  console.log(`  总结行: ${totalPieces}匹 ${totalKg}公斤 ${totalAmount}元 单价${unitPrice || '-'}元`);

  // Step 2: 逐格OCR
  console.log('\n--- Step 2: 逐格OCR ---');
  const cellW = Math.round(41 * s);
  const cellH = Math.round(22 * s);

  const cellWorkers = [];
  for (let i = 0; i < 4; i++) {
    const w = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await w.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789.' });
    cellWorkers.push(w);
  }

  const allCells = [];
  let done = 0;
  const totalCells = pageCount * 100;

  await Promise.all([...Array(totalCells)].map(async (_, idx) => {
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

    const bw = otsu(buf);
    const png = await sharp(bw, { raw: { width: 400, height: 160, channels: 1 } }).png().toBuffer();
    const { data } = await cellWorkers[idx % 4].recognize(png);

    done++;
    if (done % 100 === 0) process.stdout.write(`\r  进度: ${done}/${totalCells}`);

    const cleaned = data.text.replace(/[^0-9.]/g, '').trim();
    if (cleaned) {
      const v = parseFloat(cleaned);
      if (!isNaN(v)) allCells.push({ page: p + 1, row: ri + 7, col: ci + 1, value: v, raw: data.text.trim() });
    }
  }));

  if (done === totalCells) process.stdout.write(`\r  进度: ${done}/${totalCells} 完成\n`);
  for (const w of cellWorkers) await w.terminate();

  // Step 3: 过滤
  console.log('\n--- Step 3: 数据过滤 ---');
  const rowMap = new Map();
  for (const c of allCells) {
    if (c.value < 15 || c.value > 60) continue;
    const k = `P${c.page}R${c.row}`;
    if (!rowMap.has(k)) rowMap.set(k, { page: c.page, row: c.row, cells: [] });
    rowMap.get(k).cells.push(c);
  }

  const minCellsPerRow = (totalPieces && totalPieces <= 10) ? Math.min(3, totalPieces) : 5;
  const validRows = [...rowMap.values()].filter(r => r.cells.length >= minCellsPerRow)
    .sort((a, b) => a.page - b.page || a.row - b.row);

  let rawWeights = [];
  for (const row of validRows) {
    row.cells.sort((a, b) => a.col - b.col);
    const vals = row.cells.map(c => Math.round(c.value * 10) / 10);
    const sum = Math.round(vals.reduce((s, v) => s + v, 0) * 10) / 10;
    console.log(`  第${row.page}页第${row.row}行: ${vals.length}个 [${vals.join(', ')}] = ${sum}kg`);
    rawWeights.push(...vals);
  }

  const rawSum = Math.round(rawWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  console.log(`\n逐格提取: ${rawWeights.length}匹 ${rawSum}公斤`);

  if (rawWeights.length === 0) {
    throw new Error(`逐格OCR未提取到有效重量。图片非标准大利纺织布局。`);
  }

  // Step 4: 规格提取
  console.log('\n--- Step 4: 规格提取（逐格OCR）---');
  let products = await extractSpecDL(img, meta, s, text, ROW_Y);
  const specStr = products[0]?.spec;

  // 总公斤修正
  let finalWeights = await applyKgCorrection(rawWeights, rawSum, totalKg);

  const finalSum = Math.round(finalWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  if (totalKg) console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤 (目标${totalKg})`);
  else console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤`);

  return {
    summary: { pieces: totalPieces, kg: totalKg, amount: totalAmount, price: unitPrice },
    products,
    cells: allCells,
    weights: finalWeights,
  };
}

/** 大利标准模板规格提取 */
async function extractSpecDL(img, meta, s, text, rowY) {
  let products = [];
  let extractedWidth = null, extractedWeight = null, extractedName = null;

  const specCellW = Math.round(41 * s);
  const widthCellX = Math.round(215 * s);
  const gramCellX = Math.round(275 * s);
  const firstRowY = Math.round(rowY[0] * s);
  const cellH = Math.round(22 * s);

  if (firstRowY + cellH <= meta.height) {
    extractedWidth = await cellOCR(img, widthCellX, firstRowY, specCellW, cellH);
    extractedWeight = await cellOCR(img, gramCellX, firstRowY, specCellW, cellH);

    if (extractedWidth && (extractedWidth < 50 || extractedWidth > 300)) extractedWidth = null;
    if (extractedWeight && (extractedWeight < 50 || extractedWeight > 500)) extractedWeight = null;
  }

  // 品名
  const nameCellX = Math.round(51 * s);
  const nameCellW = Math.round(115 * s);
  if (firstRowY + cellH <= meta.height && nameCellX + nameCellW <= meta.width) {
    extractedName = await cellOCRName(img, nameCellX, firstRowY, nameCellW, cellH);
  }

  // 全页文本备用
  const { extractedWidth: fw, extractedWeight: fg, extractedName: fn } = extractSpecAndName(text);
  if (!extractedWidth) extractedWidth = fw;
  if (!extractedWeight) extractedWeight = fg;
  if (!extractedName) extractedName = fn;

  // 备用方案：逐格未取到规格时回退到全页文本
  if (!extractedWidth || !extractedWeight) {
    const lines = text.split('\n').filter(l => l.includes('|') && l.includes('公'));
    for (const line of lines) {
      if (line.includes('品名')) continue;
      const nums = line.match(/\|\s*(\d{3})\s*\|\s*(\d{2,3})\s*(?:公|kg)/i);
      if (nums) {
        const w = parseInt(nums[1]), g = parseInt(nums[2]);
        if (w > 50 && w < 300 && g > 50 && g < 500) {
          if (!extractedWidth) extractedWidth = w;
          if (!extractedWeight) extractedWeight = g;
        }
      }
      if (extractedWidth && extractedWeight) break;
    }
  }

  const specStr = (extractedWidth && extractedWeight) ? `${extractedWidth}CM${extractedWeight}G` : null;
  if (extractedName || specStr) {
    console.log(`  品名: ${extractedName || '未识别'}  规格: ${specStr || '未识别'}`);
    products.push({ name: extractedName || undefined, spec: specStr || undefined });
  } else {
    console.log('  品名/规格未从图片识别，后续由命令行参数补充');
  }

  return products;
}

// ==================== 红呈模板解析（898×454，双面板） ====================
async function parseHongcheng(img, meta) {
  console.log(`红呈双面板模板: ${meta.width}x${meta.height}`);

  // Step 1: 全页OCR
  console.log('\n--- Step 1: 全页OCR（提取品名/规格）---');
  const text = await extractSummary(img, meta);
  const { totalPieces, totalKg, totalAmount } = parseSummary(text);
  const unitPrice = (totalKg && totalAmount) ? Math.round(totalAmount / totalKg * 100) / 100 : null;
  if (totalPieces || totalKg) console.log(`  总结行: ${totalPieces||'?'}匹 ${totalKg||'?'}公斤 ${totalAmount||'?'}元`);

  const { extractedWidth, extractedWeight, extractedName } = extractSpecAndName(text);
  const specStr = (extractedWidth && extractedWeight) ? `${extractedWidth}CM${extractedWeight}G` : null;
  console.log(`  品名: ${extractedName || '未识别'}  规格: ${specStr || '未识别'}  来源: 全页OCR`);

  // Step 2: 水平投影检测数据行位置
  console.log('\n--- Step 2: 行检测（水平投影）---');
  const dataRows = await detectDataRows(img, meta, HC_DATA_AREA_Y_START, HC_DATA_AREA_Y_END, 15);
  // 跳过表头行（y<215），只保留数据行（y=215-410）
  const weightRows = dataRows.filter(r => r.center >= 215 && r.center <= 410);
  // 最多取10行（双面板模板每页10行数据）
  const dataRowsFinal = weightRows.slice(0, 10);

  console.log(`  检测到 ${dataRowsFinal.length} 行数据:`);
  for (let i = 0; i < dataRowsFinal.length; i++) {
    console.log(`    行${i+1}: y=${dataRowsFinal[i].start}-${dataRowsFinal[i].end} (中心${dataRowsFinal[i].center})`);
  }

  if (dataRowsFinal.length < 3) {
    throw new Error(`检测到 ${dataRowsFinal.length} 行数据（预期至少3行），无法继续`);
  }

  // Step 3: 逐行PSM 7 OCR提取重量（单行模式+otsu增强）
  console.log('\n--- Step 3: 逐行重量提取 ---');
  const allWeights = [];

  for (let ri = 0; ri < dataRowsFinal.length; ri++) {
    const row = dataRowsFinal[ri];
    const cropPad = ri === 0 ? 4 : 2; // 第一行多留边距
    const cropH = Math.min(meta.height - row.start + cropPad, row.end - row.start + 6 + cropPad);

    // 提取整行raw做otsu二值化
    const rowImg = await img.clone()
      .extract({ left: 0, top: Math.max(0, row.start - cropPad), width: meta.width, height: cropH })
      .resize(meta.width * 3, cropH * 3, { kernel: 'lanczos3' })
      .grayscale().normalize().sharpen(0.3);
    const rowRaw = await rowImg.raw().toBuffer();

    const bw = otsu(rowRaw);
    const png = await sharp(bw, { raw: { width: meta.width * 3, height: cropH * 3, channels: 1 } })
      .png().toBuffer();

    // PSM 7 = 单行文本
    const ws = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await ws.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789. ' });
    const { data } = await ws.recognize(png);
    await ws.terminate();

    const nums = data.text.match(/\d{2}\.\d/g);
    let vals = nums ? nums.map(n => parseFloat(n)).filter(v => v >= 15 && v <= 60) : [];

    // 不足12个时逐格补充（聚焦右侧缺失列）
    if (ri === 0 && vals.length < 12) {
      // 尝试PSM 6重试
      const ws2 = await Tesseract.createWorker('eng', 1, { logger: () => {} });
      await ws2.setParameters({ tessedit_pageseg_mode: '6', tessedit_char_whitelist: '0123456789. ' });
      const { data: d2 } = await ws2.recognize(png);
      await ws2.terminate();
      const nums2 = d2.text.match(/\d{2}\.\d/g);
      const vals2 = nums2 ? nums2.map(n => parseFloat(n)).filter(v => v >= 15 && v <= 60) : [];
      if (vals2.length > vals.length) { vals = vals2.slice(0, Math.min(vals2.length, 12)); }
      // 仍不足时，从右侧面板直接逐格提取（x=680-730区域）
      if (vals.length < 12) {
        const cell = await cellOCRWeight(img, 688, Math.max(0, row.start - 2), 46, cropH);
        if (cell && cell >= 15 && cell <= 60) vals.push(cell);
      }
    }

    console.log(`  行${ri+1} y=${row.center}: ${vals.length}个 [${vals.join(', ')}] = ${vals.reduce((s,v)=>s+v,0).toFixed(1)}kg`);
    allWeights.push(...vals);
  }

  console.log(`\n总计: ${allWeights.length}匹 ${allWeights.reduce((s,w)=>s+w,0).toFixed(1)}公斤`);

  // 补漏：半区整块OCR补充缺失值
  const hcExpected = dataRowsFinal.length * 12;
  if (allWeights.length < hcExpected) {
    console.log(`\n--- 补漏: 半区整块OCR (${allWeights.length}/${hcExpected}) ---`);
    for (const half of ['left', 'right']) {
      const hLeft = half === 'left' ? 50 : 400;
      const hW = 370;
      const halfImg = await img.clone()
        .extract({ left: hLeft, top: HC_DATA_AREA_Y_START, width: hW, height: HC_DATA_AREA_Y_END - HC_DATA_AREA_Y_START })
        .resize(hW * 4, (HC_DATA_AREA_Y_END - HC_DATA_AREA_Y_START) * 4, { kernel: 'lanczos3' })
        .grayscale().normalize().sharpen(0.5)
        .png().toBuffer();

      const ws = await Tesseract.createWorker('eng', 1, { logger: () => {} });
      await ws.setParameters({ tessedit_pageseg_mode: '6', tessedit_char_whitelist: '0123456789. ' });
      const { data } = await ws.recognize(halfImg);
      await ws.terminate();

      const nums = data.text.match(/\b\d{2}\.\d\b/g);
      if (nums) {
        const vals = nums.map(n => parseFloat(n)).filter(v => v >= 15 && v <= 60);
        const existing = new Set(allWeights.map(w => w.toFixed(1)));
        let added = 0;
        for (const v of vals) {
          const key = v.toFixed(1);
          if (!existing.has(key) && allWeights.length < hcExpected) {
            allWeights.push(v);
            existing.add(key);
            added++;
          }
        }
        if (added > 0) console.log(`  ${half}半区补充: +${added}个`);
      }
    }
    console.log(`  补充后: ${allWeights.length}匹`);
  }

  // 如果总匹数与图片总结行严重不符，用整页PSM 6抓全部数值
  if (totalPieces && allWeights.length < totalPieces * 0.5) {
    console.log(`\n--- 整页OCR回退 (${allWeights.length}/${totalPieces}) ---`);
    const big = await img.clone()
      .resize(meta.width * 4, meta.height * 4, { kernel: 'lanczos3' })
      .grayscale().normalize()
      .png().toBuffer();

    const ws = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await ws.setParameters({ tessedit_pageseg_mode: '6', tessedit_char_whitelist: '0123456789. ' });
    const { data } = await ws.recognize(big);
    await ws.terminate();

    const allNums = data.text.match(/\d{2}\.\d/g);
    if (allNums) {
      const allVals = allNums.map(n => parseFloat(n)).filter(v => v >= 15 && v <= 60);
      console.log(`  整页OCR找到 ${allVals.length} 个值`);
      if (allVals.length >= totalPieces * 0.8) {
        allWeights.length = 0;
        allWeights.push(...allVals.slice(0, totalPieces));
        console.log(`  整页OCR替代: ${allWeights.length}匹`);
      }
    }
  }

  // 规格
  let products = [];
  if (extractedName || specStr) {
    products.push({ name: extractedName || undefined, spec: specStr || undefined });
  }

  const rawSum = Math.round(allWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  let finalWeights = allWeights;
  if (totalKg && allWeights.length > 0 && Math.abs(totalKg - rawSum) > 0.05) {
    finalWeights = await applyKgCorrection(allWeights, rawSum, totalKg);
  }

  const finalSum = Math.round(finalWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  if (totalKg) console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤 (目标${totalKg})`);
  else console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤`);

  return {
    summary: { pieces: totalPieces, kg: totalKg, amount: totalAmount, price: unitPrice },
    products,
    cells: [],
    weights: finalWeights,
  };
}

// ==================== 恒泽模板解析（907×528，2行×8列） ====================
async function parseHengze(img, meta) {
  console.log(`恒泽模板: ${meta.width}x${meta.height}`);

  // Step 1: 全页OCR
  console.log('\n--- Step 1: 全页OCR ---');
  const text = await extractSummary(img, meta);
  const { totalPieces, totalKg, totalAmount } = parseSummary(text);
  const unitPrice = (totalKg && totalAmount) ? Math.round(totalAmount / totalKg * 100) / 100 : null;
  console.log(`  总结行: ${totalPieces}匹 ${totalKg}公斤 ${totalAmount}元`);

  const { extractedWidth, extractedWeight, extractedName } = extractSpecAndName(text);
  const specStr = (extractedWidth && extractedWeight) ? `${extractedWidth}CM${extractedWeight}G` : null;
  console.log(`  品名: ${extractedName || '未识别'}  规格: ${specStr || '未识别'}`);

  // Step 2: 水平投影检测数据行
  console.log('\n--- Step 2: 行检测 ---');
  const dataRows = await detectDataRows(img, meta, 130, 200, 10);
  const weightRows = dataRows.filter(r => r.center >= 135 && r.center <= 190);

  console.log(`  检测到 ${weightRows.length} 行数据`);
  for (let i = 0; i < weightRows.length; i++) {
    console.log(`    行${i+1}: y=${weightRows[i].start}-${weightRows[i].end} (中心${weightRows[i].center})`);
  }

  if (weightRows.length === 0) {
    throw new Error('恒泽模板: 未检测到数据行');
  }

  // Step 3: 逐行PSM 7 OCR提取重量（同红呈方式）
  console.log('\n--- Step 3: 逐行重量提取 ---');
  const allWeights = [];

  for (let ri = 0; ri < weightRows.length; ri++) {
    const row = weightRows[ri];
    const cropH = Math.min(meta.height - row.start + 3, row.end - row.start + 6 + 3);

    const rowRaw = await img.clone()
      .extract({ left: 0, top: Math.max(0, row.start - 3), width: meta.width, height: cropH })
      .resize(meta.width * 3, cropH * 3, { kernel: 'lanczos3' })
      .grayscale().normalize().sharpen(0.3).raw()
      .toBuffer();

    const bw = otsu(rowRaw);
    const png = await sharp(bw, { raw: { width: meta.width * 3, height: cropH * 3, channels: 1 } })
      .png().toBuffer();

    const ws = await Tesseract.createWorker('eng', 1, { logger: () => {} });
    await ws.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: '0123456789. ' });
    const { data } = await ws.recognize(png);
    await ws.terminate();

    const nums = data.text.match(/\d{2}\.\d/g);
    const vals = nums ? nums.map(n => parseFloat(n)).filter(v => v >= 15 && v <= 60) : [];

    console.log(`  行${ri+1} y=${row.center}: ${vals.length}个 [${vals.join(', ')}] = ${vals.reduce((s,v)=>s+v,0).toFixed(1)}kg`);
    allWeights.push(...vals);
  }

  console.log(`\n总计: ${allWeights.length}匹 ${allWeights.reduce((s,w)=>s+w,0).toFixed(1)}公斤`);

  // 规格
  let products = [];
  if (extractedName || specStr) {
    products.push({ name: extractedName || undefined, spec: specStr || undefined });
  }

  const rawSum = Math.round(allWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  let finalWeights = allWeights;
  if (totalKg && allWeights.length > 0 && Math.abs(totalKg - rawSum) > 0.05) {
    finalWeights = await applyKgCorrection(allWeights, rawSum, totalKg);
  }

  const finalSum = Math.round(finalWeights.reduce((s, w) => s + w, 0) * 100) / 100;
  if (totalKg) console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤 (目标${totalKg})`);
  else console.log(`最终: ${finalWeights.length}匹 ${finalSum}公斤`);

  return {
    summary: { pieces: totalPieces, kg: totalKg, amount: totalAmount, price: unitPrice },
    products,
    cells: [],
    weights: finalWeights,
  };
}

// ==================== 通用网格模板解析器 ====================
async function parseGridTemplate(img, meta) {
  console.log(`通用网格模板: ${meta.width}x${meta.height}`);
  const text = await extractSummary(img, meta);
  const { totalPieces, totalKg, totalAmount } = parseSummary(text);
  const { extractedWidth, extractedWeight, extractedName } = extractSpecAndName(text);
  const specStr = (extractedWidth && extractedWeight) ? `${extractedWidth}CM${extractedWeight}G` : null;
  console.log(`  总结: ${totalPieces||'?'}匹 ${totalKg||'?'}公斤`);
  console.log(`  品名: ${extractedName||'未识别'}  规格: ${specStr||'未识别'}`);

  const gray = await img.clone().grayscale().raw().toBuffer();
  const hRaw = [];
  for (let y = 0; y < meta.height; y++) {
    let dark = 0;
    for (let x = 0; x < meta.width; x++) if (gray[y*meta.width+x] < 100) dark++;
    if (dark > meta.width * 0.7) hRaw.push(y);
  }
  const hLines = hRaw.length > 0 ? [hRaw[0]] : [];
  for (let i = 1; i < hRaw.length; i++) if (hRaw[i] - hLines[hLines.length-1] > 2) hLines.push(hRaw[i]);
  if (hLines.length < 3) throw new Error(`网格解析: 仅检测到${hLines.length}行线`);

  const vRaw = [];
  const dT = hLines[0], dB = hLines[hLines.length-1];
  for (let x = 0; x < meta.width; x++) {
    let dark = 0;
    for (let y = dT; y < dB; y++) if (gray[y*meta.width+x] < 100) dark++;
    if (dark > (dB-dT) * 0.6) vRaw.push(x);
  }
  const vLines = vRaw.length > 0 ? [vRaw[0]] : [];
  for (let i = 1; i < vRaw.length; i++) if (vRaw[i] - vLines[vLines.length-1] > 3) vLines.push(vRaw[i]);

  console.log(`  网格: ${hLines.length}×${vLines.length}`);

  const allW = [];
  for (let ri = 1; ri < hLines.length-1; ri++) {
    const t = hLines[ri]+3, h = hLines[ri+1]-hLines[ri]-6;
    if (h < 8 || h > 40) continue;
    for (let ci = 1; ci < vLines.length-1; ci++) {
      const l = vLines[ci]+3, w = vLines[ci+1]-vLines[ci]-6;
      if (w < 15 || w > 100) continue;
      try {
        const cell = await img.clone()
          .extract({ left: l, top: t, width: w, height: h })
          .resize(200, 140, { fit: 'fill', kernel: 'lanczos3' })
          .grayscale().normalize().sharpen(0.7).png().toBuffer();
        const r = await Tesseract.recognize(cell, 'eng', { logger: () => {} });
        const ns = (r.data.text||'').match(/\d{2}\.\d/g);
        if (ns) { const v = parseFloat(ns[0]); if (v >= 10 && v <= 60) allW.push(v); }
      } catch(e) {}
    }
  }

  console.log(`  提取: ${allW.length}匹`);
  const rawSum = Math.round(allW.reduce((s,w)=>s+w,0)*100)/100;
  console.log(`总计: ${allW.length}匹 ${rawSum}公斤`);

  let products = [];
  if (extractedName || specStr) products.push({ name: extractedName || undefined, spec: specStr || undefined });

  let ow = allW;
  if (totalKg && allW.length > 0 && Math.abs(totalKg - rawSum) > 0.05) ow = await applyKgCorrection(allW, rawSum, totalKg);
  return { summary: { pieces: totalPieces, kg: totalKg }, products, cells: [], weights: ow };
}

// ==================== 总公斤修正 ====================
async function applyKgCorrection(rawWeights, rawSum, totalKg) {
  if (!totalKg || rawWeights.length === 0 || Math.abs(totalKg - rawSum) <= 0.05) {
    return rawWeights;
  }

  console.log(`\n总公斤修正: ${rawSum}kg → ${totalKg}kg (图片总结行)`);
  const factor = totalKg / rawSum;
  let finalWeights = rawWeights.map(w => Math.round(w * factor * 10) / 10);
  let diff = Math.round((totalKg - finalWeights.reduce((s, w) => s + w, 0)) * 100) / 100;

  if (Math.abs(diff) >= 0.05) {
    const errors = rawWeights.map((w, i) => ({ i, e: Math.abs(w * factor - finalWeights[i]) }))
      .sort((a, b) => b.e - a.e);
    const nudge = diff > 0 ? 0.1 : -0.1;
    for (let i = 0; i < errors.length && Math.abs(diff) >= 0.05; i++) {
      const nv = Math.round((finalWeights[errors[i].i] + nudge) * 10) / 10;
      if (nv >= 15 && nv <= 60) {
        finalWeights[errors[i].i] = nv;
        diff = Math.round((diff - nudge) * 100) / 100;
      }
    }
  }

  return finalWeights;
}

// ==================== 主入口 ====================

async function parseInvoice(imagePath) {
  const img = sharp(imagePath);
  const meta = await img.metadata();
  console.log(`图片: ${meta.width}x${meta.height}`);

  const template = detectTemplate(meta.width, meta.height);
  console.log(`模板类型: ${template}`);

  switch (template) {
    case 'DL_STANDARD':
      return parseDLStandard(img, meta);
    case 'HONGCHENG':
      return parseHongcheng(img, meta);
    case 'HENGZE':
      return parseHengze(img, meta);
    default:
      // 先尝试通用网格解析（有线框表格）
      console.log('未知模板，尝试通用网格解析...');
      try {
        return await parseGridTemplate(img, meta);
      } catch(gridErr) {
        console.log(`网格解析失败: ${gridErr.message}，尝试大利标准解析...`);
        try {
          return await parseDLStandard(img, meta);
        } catch (e) {
          throw new Error(`未知模板 (${meta.width}x${meta.height})，通用网格和大利标准均失败`);
        }
      }
  }
}

if (require.main === module) {
  (async () => {
    const imgArg = process.argv[2];
    if (!imgArg) { console.error('用法: node scripts/parse_invoice.js <图片路径>'); process.exit(1); }
    const result = await parseInvoice(path.resolve(imgArg));
    const outPath = path.join(__dirname, '..', 'temp', 'parsed_data.json');
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
    console.log(`\n已保存: ${outPath}`);
  })();
}

module.exports = { parseInvoice, detectTemplate };
