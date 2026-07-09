#!/usr/bin/env node
/**
 * 大利纺织销售单生成器
 *
 * 用法：node scripts/make_sales_order.js --json data.json
 *
 * 完全保留原表排版/字体/行距/合并单元格。
 * 核心策略：彻底删除空行cell（不设空字符串，避免COUNTA误计数），
 * 只保留公式cell（R匹数/S数量/U金额），重建时写cache=0。
 */
const XLSX = require('xlsx');
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { copyStyles } = require('./xlsx_style_copier');

const BASE = path.resolve(__dirname, '..');
const TEMPLATE = path.join(BASE, 'data', 'msg', 'file', '2026-04', '大利纺织销售单-TEMPLATE.xlsx');
const OUTPUT_DIR = path.join(BASE, 'data', 'msg', 'file');

// ========== 工具 ==========

/** 精确保留2位小数，避免IEEE 754浮点误差 */
function r2(n) { return Math.round((n || 0) * 100 + Number.EPSILON * 100) / 100; }

function dateSerial(date) {
  return (date - new Date(1899, 11, 30)) / 86400000;
}

/** 全局最大NO号 */
function getNextNO() {
  if (!fs.existsSync(OUTPUT_DIR)) return 1;
  let max = 0;
  for (const f of fs.readdirSync(OUTPUT_DIR)) {
    if (!f.includes('大利纺织销售单')) continue;
    const m = f.match(/NO\.?\s*(\d+)/i);
    if (m) max = Math.max(max, parseInt(m[1]));
  }
  return max + 1;
}

/** 回头客自动续号：查该客户上次NO，有则+1，无则全局+1 */
function getNextNOForCustomer(customer) {
  if (!fs.existsSync(OUTPUT_DIR)) return 1;
  let customerMax = 0, globalMax = 0;
  for (const f of fs.readdirSync(OUTPUT_DIR)) {
    if (!f.includes('大利纺织销售单')) continue;
    const noMatch = f.match(/NO\.?\s*(\d+)/i);
    if (!noMatch) continue;
    const no = parseInt(noMatch[1]);
    globalMax = Math.max(globalMax, no);
    try {
      const wb = XLSX.readFile(path.join(OUTPUT_DIR, f), { sheets: ['大利纺织'] });
      const ws = wb.Sheets['大利纺织'];
      if (ws && ws['A5']) {
        const name = (ws['A5'].v || '').toString().replace('名称：', '').trim();
        if (name === customer) customerMax = Math.max(customerMax, no);
      }
    } catch(e) { /* skip unreadable files */ }
  }
  if (customerMax > 0) {
    console.log(`  回头客「${customer}」上次NO.${customerMax}，续号NO.${customerMax+1}`);
    return customerMax + 1;
  }
  console.log(`  新客户「${customer}」，默认NO.1`);
  return 1;
}

function genOrderNo() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  const rand = String(Math.floor(Math.random() * 450) + 50).padStart(3, '0');
  return `${y}${m}${d}${rand}`;
}

// ========== 规则2 ==========
function applyWeightRule(products, totalWeights) {
  if (totalWeights <= 20) return products;
  const flat = [];
  for (const prod of products) for (const w of prod.weights)
    flat.push({ weight: w, pid: products.indexOf(prod) });

  // 规则：总匹数×0.2=总加重量，分配到随机匹各+1kg
  const totalExtra = Math.floor(totalWeights * 0.2);
  const indices = [...Array(flat.length).keys()];
  // 打乱取前totalExtra个
  for (let i = indices.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [indices[i], indices[j]] = [indices[j], indices[i]];
  }
  for (let i = 0; i < totalExtra && i < indices.length; i++) {
    flat[indices[i]].weight += 1;
  }

  return products.map((p, idx) => ({ ...p, weights: flat.filter(f => f.pid === idx).map(f => f.weight) }));
}

// ========== HTML预览 ==========
function generateHtmlPreview({ customer, no, orderNo, products, totalWeights, sheetCount }) {
  const dr = [];
  for (const prod of products) for (const w of prod.weights) dr.push({ ...prod, weight: w });

  let html = '';
  for (let si = 0, st = 0; st < dr.length; si++, st += 100) {
    const sl = dr.slice(st, st + 100);
    const nl = si === 0 ? `NO.${no}` : `NO.${no}续${si + 1}`;
    html += `<tr class="shead"><td colspan="5">Sheet ${si + 1} (${nl})</td></tr>`;
    html += `<tr><th>品名</th><th>规格</th><th>颜色</th><th>单位</th><th>重量</th></tr>`;
    for (let i = 0; i < sl.length; i += 10) {
      const rs = sl.slice(i, i + 10);
      const f = rs[0];
      html += `<tr><td style="text-align:left">${f.name}</td><td>${f.spec}</td><td>${f.color}</td><td>${f.unit||'公斤'}</td><td style="text-align:left">${rs.map(w=>w.weight).join(', ')}</td></tr>`;
    }
  }
  const full = `<!DOCTYPE html><html><meta charset="utf-8"><style>
*{margin:0;padding:0}body{font-family:微软雅黑,sans-serif;padding:20px}
h2{margin-bottom:8px}.info{font-size:14px;margin-bottom:15px;color:#333}
.info span{margin-right:24px}table{border-collapse:collapse;width:100%}
th,td{border:1px solid #999;padding:4px 8px;font-size:13px;text-align:center}
th{background:#4472C4;color:#fff;font-weight:bold}
.shead td{background:#f0f0f0;font-weight:bold;padding:6px;font-size:14px}
</style><body><h2>大利纺织销售单</h2>
<div class="info"><span>客户：${customer}</span><span>单号：XFS${orderNo}</span>
<span>日期：${new Date().toLocaleDateString()}</span><span>NO.${no}</span>
<span>总匹数：${totalWeights}</span><span>Sheet数：${sheetCount}</span></div><table>${html}</table>
<p style="color:#999;font-size:12px;margin-top:20px">← 预览数据，xlsx以Excel打开为准</p></body></html>`;
  const hp = path.join(OUTPUT_DIR, `大利纺织预览-XFS${orderNo}-NO${no}.html`);
  fs.writeFileSync(hp, full, 'utf-8');
  return hp;
}

// ========== 主生成 ==========

async function generate({ customer, no, products, verify = true }) {
  if (!products || !products.length) throw new Error('至少需要一个产品');
  if (!customer) throw new Error('客户名不能为空');
  const totalWeights = products.reduce((s, p) => s + p.weights.length, 0);
  if (totalWeights === 0) throw new Error('至少需要一个重量');

  const adjusted = applyWeightRule(products, totalWeights);
  const sheetCount = Math.ceil(totalWeights / 100);
  const orderNo = genOrderNo();
  const noFinal = no || getNextNOForCustomer(customer);
  const today = new Date();

  if (!fs.existsSync(TEMPLATE)) throw new Error(`模板不存在: ${TEMPLATE}`);
  const wb = XLSX.readFile(TEMPLATE, { cellNF: true, cellFormula: true, cellStyles: true, sheetStubs: true, raw: true });

  const fw = [];
  for (const p of adjusted) for (const w of p.weights) fw.push({ weight: w, product: p });

  const DATA_START = 7, ROWS = 10, COLS = 10;
  // 汇总行公式地址：B17=SUM(R7:R16), D17=SUM(S7:S16), I17=SUM(U7:U16)
  // 加上 已付/未付/累计 等
  const TOTAL_ROW = 17;

  // ===== 第一遍：收集每页小计，计算总计 =====
  const pageTotals = [];

  for (let si = 0; si < sheetCount; si++) {
    const name = si === 0 ? '大利纺织' : `大利纺织(${si + 1})`;
    let ws;
    if (si === 0) {
      ws = wb.Sheets['大利纺织'];
    } else {
      ws = JSON.parse(JSON.stringify(wb.Sheets['大利纺织']));
      for (const k of ['!ref', '!merges', '!cols', '!rows']) {
        if (wb.Sheets['大利纺织'][k]) ws[k] = JSON.parse(JSON.stringify(wb.Sheets['大利纺织'][k]));
      }
      wb.Sheets[name] = ws;
      wb.SheetNames.push(name);
    }

    // ===== 清空数据行7-16（保留边框样式） =====
    for (let r = DATA_START; r < DATA_START + ROWS; r++) {
      for (let c = 1; c <= 21; c++) {
        const addr = XLSX.utils.encode_cell({ r: r - 1, c: c - 1 });
        const cell = ws[addr];
        if (!cell) continue;
        if (cell.f) {
          cell.v = 0;
          cell.w = '0';
          cell.t = 'n';
        } else {
          cell.t = 'z';
          delete cell.v;
          delete cell.w;
          delete cell.h;
        }
      }
    }

    // ===== 表头 =====
    if (ws['A5']) { ws['A5'].t = 's'; ws['A5'].v = `名称：${customer}`; }
    if (ws['K5']) { ws['K5'].t = 's'; ws['K5'].v = `NO.${noFinal}`; }
    if (ws['T3']) { ws['T3'].t = 's'; ws['T3'].v = `XFS${orderNo}`; }
    if (ws['T4']) { ws['T4'].t = 'n'; ws['T4'].v = dateSerial(today); ws['T4'].z = 'yyyy\\-m\\-d'; }

    // 当前页/总页（模板Row21已有"当前： 总页："）
    if (ws['B21']) { ws['B21'].t = 'n'; ws['B21'].v = si + 1; }
    if (ws['E21']) { ws['E21'].t = 'n'; ws['E21'].v = sheetCount; }

    // ===== 填充数据 =====
    const st = si * 100;
    let pageRolls = 0, pageKg = 0, pageAmount = 0;
    let weightIdx = st; // 当前重量索引

    for (let ro = 0; ro < ROWS; ro++) {
      if (weightIdx >= totalWeights) break;

      const rowNum = DATA_START + ro;
      // 取当前产品的连续重量（同产品才能同行）
      const startProd = fw[weightIdx]?.product;
      const rowWeights = [];
      while (weightIdx < totalWeights && rowWeights.length < COLS) {
        const w = fw[weightIdx];
        if (rowWeights.length > 0 && w.product !== startProd) break; // 不同产品换行
        rowWeights.push(w);
        weightIdx++;
      }

      const fp = rowWeights[0]?.product;
      const isFirstRowOfProduct = ro === 0 || startProd !== fw[Math.max(0, weightIdx - rowWeights.length - 1)]?.product;
      const isNP = ro === 0 || (rowWeights.length > 0 && isFirstRowOfProduct);

      // A=品名, D=规格, F=颜色, G=单位, T=单价（修改in-place保留样式边框）
      if (isNP) {
        ws[`A${rowNum}`].t = 's'; ws[`A${rowNum}`].v = fp.name;
        ws[`D${rowNum}`].t = 's'; ws[`D${rowNum}`].v = fp.spec || '';
      }
      // 颜色、单位、单价每行都要写（同规格同单价）
      ws[`F${rowNum}`].t = 's'; ws[`F${rowNum}`].v = fp.color || '';
      ws[`G${rowNum}`].t = 's'; ws[`G${rowNum}`].v = fp.unit || '公斤';
      if (fp.price) { ws[`T${rowNum}`].t = 'n'; ws[`T${rowNum}`].v = fp.price; }

      // H-Q = 重量列（修改in-place保留样式边框）
      let rowSum = 0, rowCount = 0;
      rowWeights.forEach((w, i) => {
        if (w.weight && w.weight > 0) {
          const cell = ws[`${String.fromCharCode(72 + i)}${rowNum}`];
          if (cell) { cell.t = 'n'; cell.v = w.weight; }
          rowSum += w.weight;
          rowCount++;
        }
      });
      // 更新公式cache
      if (ws[`R${rowNum}`]) { ws[`R${rowNum}`].v = rowCount; ws[`R${rowNum}`].w = String(rowCount); }
      if (ws[`S${rowNum}`]) { ws[`S${rowNum}`].v = Math.round(rowSum * 100) / 100; ws[`S${rowNum}`].w = String(Math.round(rowSum * 100) / 100); }
      if (ws[`U${rowNum}`] && ws[`T${rowNum}`]) {
        ws[`U${rowNum}`].v = Math.round(rowSum * fp.price * 100) / 100;
        ws[`U${rowNum}`].w = String(Math.round(rowSum * fp.price * 100) / 100);
      }

      pageRolls += rowCount;
      pageKg += rowSum;
      pageAmount += rowSum * (fp.price || 0);
    }

    pageTotals.push({ rolls: pageRolls, kg: pageKg, amount: pageAmount });
  }

  // ===== 第二遍：写入公式cache（每页小计 + 总计） =====
  const allRolls = pageTotals.reduce((s, p) => s + p.rolls, 0);
  const allKg = r2(pageTotals.reduce((s, p) => s + p.kg, 0));
  const allAmount = r2(pageTotals.reduce((s, p) => s + p.amount, 0));

  for (let si = 0; si < sheetCount; si++) {
    const name = si === 0 ? '大利纺织' : `大利纺织(${si + 1})`;
    const ws = wb.Sheets[name];
    if (!ws) continue;

    const pt = pageTotals[si];
    const pageAmtR = r2(pt.amount);

    // 汇总行(17)：本页小计
    if (ws['B17'] && ws['B17'].f) { ws['B17'].v = pt.rolls; ws['B17'].w = String(pt.rolls); }
    if (ws['D17'] && ws['D17'].f) { ws['D17'].v = r2(pt.kg); ws['D17'].w = String(r2(pt.kg)); }
    if (ws['I17'] && ws['I17'].f) { ws['I17'].v = pageAmtR; ws['I17'].w = String(pageAmtR); }

    // 应付行：每页都是整单总金额
    if (ws['S17'] && ws['S17'].f) { ws['S17'].v = allAmount; ws['S17'].w = String(allAmount); }
    if (ws['S18'] && ws['S18'].f) { ws['S18'].v = allAmount; ws['S18'].w = String(allAmount); }
    if (ws['S19'] && ws['S19'].f) { ws['S19'].v = allAmount; ws['S19'].w = String(allAmount); }

    // U1（本单应付）：每页都是整单总金额
    if (ws['U1'] && ws['U1'].f) { ws['U1'].v = allAmount; ws['U1'].w = String(allAmount); }
  }

  // 保存
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const filename = `大利纺织销售单-XFS${orderNo}-NO${noFinal}.xlsx`;
  const outPath = path.join(OUTPUT_DIR, filename);
  XLSX.writeFile(wb, outPath);
  // 后处理：从模板复制cell s属性（边框/字体/行高），非致命
  try { await copyStyles(TEMPLATE, outPath); } catch (e) { console.log('  ⚠ style copy:', e.message); }

  // 多页时：U1公式=I17+M17会重算成单页小计，必须去掉公式保留硬值=整单总计
  if (sheetCount > 1) {
    try { await fixFormulaU1(outPath, sheetCount); } catch (e) { console.log('  ⚠ U1 fix:', e.message); }
  }

  const hp = generateHtmlPreview({ customer, no: noFinal, orderNo, products: adjusted, totalWeights, sheetCount });
  console.log(`  ✓ html预览已生成`);

  // 货款催收文案 → 剪贴板
  const totalAmount = allAmount.toFixed(2);
  const paymentMsg = `你好，麻烦结下货款${totalAmount}`;
  try {
    require('child_process').execSync(`echo ${paymentMsg} | clip`, { stdio: 'ignore' });
    console.log(`  ✓ 催收文案已复制到剪贴板: "${paymentMsg}"`);
  } catch (e) {
    console.log(`  ⚠ 剪贴板复制失败: ${e.message}`);
  }

  return { path: outPath, htmlPath: hp, filename, no: noFinal, orderNo, sheetCount, totalWeights, totalAmount, rule2Applied: totalWeights > 20 };
}

// ========== 交互 ==========
async function interactive() {
  console.log('\n═══════════════════════════════\n   大利纺织 · 销售单生成\n═══════════════════════════════\n');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const ask = q => new Promise(r => rl.question(q, r));
  const customer = await ask('客户名: ') || '';
  const no = getNextNO();
  console.log(`NO.${no}\n`);
  const products = [];
  console.log('--- 输入产品（产品名留空结束）---');
  while (true) {
    const name = await ask(`产品${products.length + 1}名: `);
    if (!name) break;
    const spec = await ask('  规格: ');
    const color = await ask('  颜色: ');
    const unit = await ask('  单位 [公斤]: ') || '公斤';
    const p = await ask('  单价(留空不填): ');
    const price = parseFloat(p) || undefined;
    const raw = (await ask('  重量(空格分隔): ')).trim();
    const weights = raw ? raw.split(/\s+/).filter(Boolean).map(Number) : [];
    if (!weights.length) { console.log('  ⚠ 需至少1个重量'); continue; }
    products.push({ name, spec, color, unit, price, weights });
    console.log(`  → ${name} (${weights.length}匹)\n`);
  }
  rl.close();
  if (!products.length) { console.log('未输入产品'); return; }
  console.log('\n--- 生成中 ---');
  const r = await generate({ customer, no, products });
  console.log(`\n✅ ${r.filename}\n  客户: ${customer}  单号: XFS${r.orderNo}  NO.${r.no}\n  匹数: ${r.totalWeights}${r.rule2Applied ? ' (规则2已应用)' : ''}\n  xlsx: ${r.path}\n  html: ${r.htmlPath}`);
}

/** 多页时去掉U1的公式（I17+M17只会算单页），保持硬值=整单总计 */
async function fixFormulaU1(xlsxPath, sheetCount) {
  const JSZip = require('jszip');
  const z = await JSZip.loadAsync(require('fs').readFileSync(xlsxPath));
  for (let si = 1; si <= sheetCount; si++) {
    const key = 'xl/worksheets/sheet' + si + '.xml';
    if (!z.file(key)) continue;
    let xml = await z.file(key).async('string');
    xml = xml.replace(/(<c[^>]*?r="U1"[^>]*?>)\s*<f[^>]*>[\s\S]*?<\/f>\s*/g, '$1');
    z.file(key, xml);
  }
  require('fs').writeFileSync(xlsxPath, await z.generateAsync({ type: 'nodebuffer' }));
}

if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.length === 0) interactive();
  else if (args[0] === '--json' && args[1]) {
    (async () => {
      try { const input = JSON.parse(fs.readFileSync(args[1], 'utf-8')); const r = await generate(input); console.log(JSON.stringify(r, null, 2)); }
      catch (e) { console.error(`错误: ${e.message}`); process.exit(1); }
    })();
  } else {
    (async () => {
      try { const r = await generate(JSON.parse(args[0])); console.log(JSON.stringify(r, null, 2)); }
      catch (e) { console.error(`用法: node scripts/make_sales_order.js --json data.json\n      node scripts/make_sales_order.js              # 交互模式`); process.exit(1); }
    })();
  }
}

module.exports = { generate, getNextNO, genOrderNo, applyWeightRule };
