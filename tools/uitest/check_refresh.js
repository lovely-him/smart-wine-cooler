// 用途: 验证页面刷新策略（数据不变不重绘 / 编辑中冻结表单 / 点此更新强制应用）。
// 做法: 用最小 DOM 桩把 index.html 里的脚本原样跑起来，数 innerHTML 的写入次数。
// 依赖: node。用法见同目录 run.sh。

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ── 路径与常量 ──
const INDEX = path.join(__dirname, '../../services/web/static/index.html');

// ── 桩 ──
function makeDom() {
  const writes = {};
  const elements = {};
  const doc = {
    getElementById: id => {
      if (elements[id]) return elements[id];
      const node = { id, _html: '', textContent: '', style: {}, scrollTop: 0, scope: id };
      Object.defineProperty(node, 'innerHTML', {
        get: () => node._html,
        set: value => {
          node._html = value;
          writes[id] = (writes[id] || 0) + 1;
        },
      });
      node.contains = other => !!other && other.scope === node.scope;
      elements[id] = node;
      return node;
    },
    querySelectorAll: () => [],
    addEventListener: () => {},
    activeElement: null,
  };
  return { doc, elements, writes };
}

function makeSnapshot(count) {
  return {
    status: {
      cameras: [{ id: 'layer1', name: 'L1', state: 'idle', error: '', observation: null }],
      scan: { id: 0, state: 'idle', finished: {}, note: '' },
      pending: { diff: [], baseline: null },
      categories: [{ id: 5, name: '可乐', refs: 3 }],
    },
    view: {
      baseline_names: { layer1: { 可乐: count } },
      reported_names: { layer1: { 可乐: count } },
      draft: {
        layer1: Array.from({ length: count }, (_, index) => ({
          index, crop_url: '', name: '可乐', known: true, cluster: null, score: 0.9,
        })),
      },
      observations: [],
    },
    history: { observations: [], events: [], confirmations: [] },
  };
}

// ── main ──
async function main() {
  const html = fs.readFileSync(INDEX, 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!script) {
    console.log('[FAIL] index.html 里没找到脚本块');
    return 1;
  }

  const { doc, elements, writes } = makeDom();
  const box = { snapshot: makeSnapshot(2) };
  const fetchStub = async url => {
    if (box.fail) throw new Error('网络中断');
    if (box.gate) await box.gate;
    if (url === '/api/status') box.statusCalls = (box.statusCalls || 0) + 1;
    const table = {
      '/api/status': () => box.snapshot.status,
      '/api/view': () => box.snapshot.view,
      '/api/history': () => box.snapshot.history,
      '/api/categories/rename': () => ({ ok: 'x' }),
      '/api/categories/delete': () => ({ id: 5, deleted: true }),
      '/api/categories/merge': () => ({ ok: 1 }),
    };
    const make = table[url] || (() => ({}));
    return { ok: true, json: async () => JSON.parse(JSON.stringify(make())) };
  };

  const ctx = {
    document: doc, fetch: fetchStub, alert: () => {}, confirm: () => true,
    setInterval: () => 0, setTimeout: f => f(),
    console, JSON, Object, Math, Date, parseInt,
  };
  vm.createContext(ctx);
  vm.runInContext(script[1], ctx);

  const confirmWrites = () => writes.confirmForm || 0;
  const fails = [];
  const check = (ok, message) => {
    console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${message}`);
    if (!ok) fails.push(message);
  };

  console.log('\n【刷新策略】');
  // 页面加载时就发起了一次刷新，先等它跑完（锁保证这时不会叠加第二次）
  const flush = () => new Promise(done => setImmediate(done));
  for (let i = 0; i < 50 && confirmWrites() === 0; i++) await flush();
  await flush();
  const first = confirmWrites();
  check(first === 1, `首次渲染表单（实际 ${first} 次）`);

  await ctx.refresh();
  check(confirmWrites() === 1, `数据没变不重绘（实际 ${confirmWrites()} 次）`);

  box.snapshot = makeSnapshot(3);
  await ctx.refresh();
  check(confirmWrites() === 2, `数据变化才重绘（实际 ${confirmWrites()} 次）`);

  doc.activeElement = { scope: 'confirmForm' };
  box.snapshot = makeSnapshot(5);
  await ctx.refresh();
  check(confirmWrites() === 2, `表单内有焦点时不重绘（实际 ${confirmWrites()} 次）`);
  check(elements.staleBar.style.display === '', '顶部提示条已显示');
  check(elements.staleText.textContent.includes('未自动更新'), `提示文案: ${elements.staleText.textContent}`);

  await ctx.applyStale();
  check(confirmWrites() === 3, `点「点此更新」强制应用（实际 ${confirmWrites()} 次）`);
  check(elements.staleBar.style.display === 'none', '应用后提示条隐藏');

  // 按钮操作（改名/合并/删除）是明确的意图：即使焦点落在按钮上也要立刻重绘
  doc.activeElement = { scope: 'cats' };
  box.snapshot = makeSnapshot(5);
  box.snapshot.status.categories = [{ id: 5, name: '可乐', refs: 3 }];
  const catsBefore = writes.cats || 0;
  await ctx.deleteCat(5);
  check((writes.cats || 0) > catsBefore, `按钮操作后强制重绘类别表（${catsBefore} → ${writes.cats}）`);

  console.log('\n【取数失败与并发】');
  // 板端一轮刷新可能超过 2 百秒的轮询间隔：上一轮没回来时不能叠加，否则旧响应会覆盖新响应
  let release;
  box.gate = new Promise(done => { release = done; });
  const callsBefore = box.statusCalls || 0;
  const running = ctx.refresh();
  const overlapped = ctx.refresh();
  release();
  await Promise.all([running, overlapped]);
  box.gate = null;
  const rounds = (box.statusCalls || 0) - callsBefore;
  check(rounds === 1, `上一轮未完成时不叠加请求（/api/status 本轮调用 ${rounds} 次）`);

  const writesBeforeFail = confirmWrites();
  box.fail = true;
  await ctx.refreshNow();
  check(
    elements.hint.textContent.includes('刷新失败'),
    `失败只提示不报错: ${elements.hint.textContent}`
  );
  check(confirmWrites() === writesBeforeFail, '失败时不动已渲染的内容');

  box.fail = false;
  doc.activeElement = { scope: 'confirmForm' };
  box.snapshot = makeSnapshot(7);
  await ctx.refresh();
  check(
    confirmWrites() === writesBeforeFail,
    `失败后恢复正常也不覆盖正在编辑的表单（实际 ${confirmWrites()} 次）`
  );

  console.log(fails.length ? `\n[FAIL] ${fails.length} 项不通过` : '\n[ OK ] 刷新策略符合预期');
  return fails.length ? 1 : 0;
}

main().then(code => process.exit(code));
