// Injected into the game frame. Reads game state and screen geometry; the only
// thing it changes is the camera position (a purely client-side view).
(() => {
  // replaced with a hash of this file when injected, so edits always take effect
  const VERSION = '__AGENT_VERSION__';
  const prev = window.__fmvAgent;
  if (prev && prev.version === VERSION) return 'present';

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isObj = (v) => v !== null && typeof v === 'object';
  const tryOr = (fn, dflt) => { try { const v = fn(); return v === undefined ? dflt : v; } catch (e) { return dflt; } };
  // members that are methods in one build can be getters in another
  const val = (o, k) => (typeof o[k] === 'function' ? o[k]() : o[k]);

  // an older agent already found the game: keep its handles instead of searching again
  const A = { version: VERSION, g: prev ? prev.g : null, s: null, sceneObj: null, mc: null };

  // ---------------------------------------------------------------- discovery

  // only the game's own bundle; other code in the frame (ads, SDKs) is left alone
  function chunkGlobals() {
    return Object.getOwnPropertyNames(self).filter((k) => /^webpackChunk.*(farm|merge)/i.test(k) && Array.isArray(self[k]));
  }

  // every webpack runtime sharing a chunk global gets the pushed callback, newest runtime first
  function runtimes() {
    const reqs = [];
    for (const k of chunkGlobals()) {
      self[k].push([[`fmv${Date.now()}${Math.random()}`], {}, (r) => { if (!reqs.includes(r)) reqs.push(r); }]);
    }
    return reqs;
  }

  function containerClass(r) {
    for (const id of Object.keys(r.m || {})) {
      let src;
      try { src = Function.prototype.toString.call(r.m[id]); } catch (e) { continue; }
      if (!src.includes('sortableChildren') || !src.includes('addChildAt')) continue;
      let ex;
      try { ex = r(id); } catch (e) { continue; }
      if (!ex) continue;
      const cands = [ex];
      for (const k of Object.keys(ex)) { try { cands.push(ex[k]); } catch (e) { /* lazy export */ } }
      for (const v of cands) {
        if (typeof v === 'function' && v.prototype
          && Object.getOwnPropertyDescriptor(v.prototype, 'sortChildren')
          && Object.getOwnPropertyDescriptor(v.prototype, 'updateTransform')) return v;
      }
    }
    return null;
  }

  // Pixi gives the root a temporary parent while rendering, so walk up while parents have children
  async function captureRoot(C, timeout) {
    const desc = Object.getOwnPropertyDescriptor(C.prototype, 'updateTransform');
    let root = null;
    C.prototype.updateTransform = function () {
      if (!root) { let t = this; while (t.parent && t.parent.children) t = t.parent; root = t; }
      return desc.value.apply(this, arguments);
    };
    try {
      const end = Date.now() + timeout;
      while (!root && Date.now() < end) await sleep(25);
    } finally {
      Object.defineProperty(C.prototype, 'updateTransform', desc);
    }
    return root;
  }

  function bfs(start, pred, maxVisits) {
    const seen = new Set([start]);
    const q = [start];
    for (let i = 0; i < q.length && i < maxVisits; i++) {
      const o = q[i];
      let keys;
      try { keys = Object.keys(o); } catch (e) { continue; }
      for (const k of keys) {
        let v;
        try { v = o[k]; } catch (e) { continue; }
        if (!isObj(v) || seen.has(v)) continue;
        if (ArrayBuffer.isView(v) || (typeof Node !== 'undefined' && v instanceof Node)) continue;
        seen.add(v);
        try { if (pred(v)) return v; } catch (e) { /* ignore */ }
        q.push(v);
      }
    }
    return null;
  }

  const isGlobalServices = (o) => isObj(o.inventory) && typeof o.inventory.getAmount === 'function'
    && isObj(o.navigation) && isObj(o.canvas);

  function findScene() {
    const stack = [A.g.canvas.stage];
    let best = null;
    while (stack.length) {
      const o = stack.pop();
      if (o.services && isObj(o.services.mapGrid) && isObj(o.services.gridFilter) && !o._destroyed) {
        if (o.worldVisible) return o;
        best = best || o;
      }
      if (o.children) for (const c of o.children) stack.push(c);
    }
    return best;
  }

  function refreshScene() {
    if (!A.g) return false;
    if (A.sceneObj && !A.sceneObj._destroyed && A.sceneObj.worldVisible && A.sceneObj.parent) return true;
    A.sceneObj = findScene();
    A.s = A.sceneObj ? A.sceneObj.services : null;
    A.mc = null;
    return !!A.s;
  }

  A.init = async function (timeout) {
    timeout = timeout || 4000;
    if (A.g && A.g.canvas && A.g.canvas.stage && !A.g.canvas.stage._destroyed) {
      refreshScene();
      return A.status();
    }
    const reqs = runtimes();
    if (!reqs.length) throw new Error('no webpack runtime in this frame');
    let root = null;
    for (const r of reqs) {
      const C = containerClass(r);
      if (!C) continue;
      root = await captureRoot(C, timeout);
      if (root) break;
    }
    if (!root) throw new Error('could not capture the Pixi stage - is the game visible and rendering?');
    const g = bfs(root, isGlobalServices, 400000);
    if (!g) throw new Error('found the stage but not the game services');
    A.g = g;
    refreshScene();
    return A.status();
  };

  A.status = function () {
    return {
      version: VERSION,
      ready: !!A.g,
      scene: !!A.s,
      sceneName: A.sceneObj ? (A.sceneObj.name || A.sceneObj._name || '') : null,
      globalServices: A.g ? Object.keys(A.g) : [],
      sceneServices: A.s ? Object.keys(A.s) : [],
    };
  };

  // ---------------------------------------------------------------- geometry

  function geom() {
    const cv = A.g.canvas;
    const rect = cv.view.getBoundingClientRect();
    const scr = cv.renderer.screen;
    return { l: rect.left, t: rect.top, w: rect.width, h: rect.height, sx: rect.width / scr.width, sy: rect.height / scr.height, vw: innerWidth, vh: innerHeight };
  }

  const css = (p, G) => ({ x: Math.round((G.l + p.x * G.sx) * 10) / 10, y: Math.round((G.t + p.y * G.sy) * 10) / 10 });

  function mapContainer() {
    if (A.mc && !A.mc._destroyed && A.mc.parent) return A.mc;
    for (const cell of A.s.mapGrid.cells.values()) {
      const o = cell._content;
      if (o && o.parent && o.parent.worldTransform) { A.mc = o.parent; return A.mc; }
    }
    return null;
  }

  // the projection gives a tile's top corner; its centre is halfway to the bottom corner,
  // which is the top corner of the tile diagonally below
  function vertex(c, r) {
    return mapContainer().worldTransform.apply(A.s.axonometricProjection.getWorldPosition(c, r));
  }

  function areaCentre(c0, r0, c1, r1, G) {
    const top = vertex(c0, r0);
    const bottom = vertex(c1 + 1, r1 + 1);
    return css({ x: (top.x + bottom.x) / 2, y: (top.y + bottom.y) / 2 }, G);
  }

  function cellPoint(c, r, G) {
    return areaCentre(c, r, c, r, G);
  }

  function boundsCss(o, G) {
    const b = o.getBounds();
    return { x: Math.round(G.l + (b.x + b.width / 2) * G.sx), y: Math.round(G.t + (b.y + b.height / 2) * G.sy), w: Math.round(b.width * G.sx), h: Math.round(b.height * G.sy) };
  }

  A.cellCss = function (c, r) {
    if (!refreshScene()) return null;
    return cellPoint(c, r, geom());
  };

  // A point inside tile (c, r) where a press reaches the object standing on it. Objects have tall
  // rectangular hit areas, so a building in front can cover the tile behind it; like the game, the
  // object drawn on top wins. Popouts (small menus) cover everything under them.
  A.pickPoint = function (c, r) {
    if (!refreshScene()) return null;
    const G = geom();
    const cell = A.s.mapGrid.getCell(c, r);
    const target = cell && cell._content;
    const top = vertex(c, r), bottom = vertex(c + 1, r + 1), left = vertex(c, r + 1), right = vertex(c + 1, r);
    const ctr = { x: (top.x + bottom.x) / 2, y: (top.y + bottom.y) / 2 };
    const lerp = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
    const cands = [ctr, lerp(ctr, top, 0.4), lerp(ctr, bottom, 0.4), lerp(ctr, left, 0.5), lerp(ctr, right, 0.5), lerp(ctr, top, 0.75)];
    const mc = mapContainer();
    const objs = mc ? mc.children.filter((o) => o._behaviors && o.hitArea && o.visible) : [];
    const popoutRoot = tryOr(() => A.s.popout._targetContainer, null);
    const popoutBoxes = popoutRoot ? popoutRoot.children.filter((p) => p.visible && p.worldAlpha > 0.05).map((p) => p.getBounds()) : [];
    const topAt = (p) => {
      let best = null;
      for (let i = objs.length - 1; i >= 0; i--) {
        const o = objs[i];
        const l = o.worldTransform.applyInverse(p);
        if (o.hitArea.contains(l.x, l.y)) { best = o; break; }
      }
      return best;
    };
    // cells under a big building hold a placeholder; pressing the building itself is what counts
    const covers = (o) => {
      // an empty tile only works where nothing else's hit area reaches over it
      if (!target) return o === null;
      if (o === target) return true;
      if (!o || !target._behaviors.has('extendedTileChild') || !o._behaviors.has('extendedTile')) return false;
      const gp = o._behaviors.get('gridPosition');
      const ext = o._behaviors.get('extendedTile');
      if (!gp) return false;
      const w = tryOr(() => ext.width, 1), h = tryOr(() => ext.height, 1);
      return c <= gp.column && c > gp.column - w && r <= gp.row && r > gp.row - h;
    };
    let blocker = null;
    let popout = false;
    for (const p of cands) {
      if (popoutBoxes.some((b) => b.contains(p.x, p.y))) { popout = true; continue; }
      const o = topAt(p);
      if (covers(o)) return { ...css(p, G), ok: true };
      if (!blocker && o) blocker = o._blueprintID;
    }
    return { ...css(ctr, G), ok: false, blocker, popout };
  };

  A.camera = function () {
    if (!refreshScene()) return null;
    const mc = mapContainer();
    if (!mc) return null;
    const t = mc.worldTransform;
    return { a: t.a, tx: t.tx, ty: t.ty };
  };

  A.focusCell = function (c, r) {
    if (!refreshScene()) return false;
    const cell = A.s.mapGrid.getCell(c, r);
    if (!cell) return false;
    A.s.cameraService.teleportCameraToCell(cell);
    return true;
  };

  // ---------------------------------------------------------------- state

  const SKIP_DATA = new Set(['harvestReward', 'sprite', 'crateRewardContent', 'steps', 'sounds', 'animation', 'spine']);

  function prune(v, depth) {
    if (!isObj(v)) return typeof v === 'function' ? undefined : v;
    if (depth <= 0) return undefined;
    if (Array.isArray(v)) return v.slice(0, 40).map((x) => prune(x, depth - 1));
    const out = {};
    for (const k of Object.keys(v)) {
      if (SKIP_DATA.has(k) || k.startsWith('_')) continue;
      const pv = prune(v[k], depth - 1);
      if (pv !== undefined) out[k] = pv;
    }
    return out;
  }

  function timerInfo(id) {
    const t = tryOr(() => A.g.timer.getTimerByID(id), null);
    if (!t) return null;
    return { id, label: t._label, state: t._state, remaining: t._remaining, duration: t._duration };
  }

  function objectState(o, c, r, G) {
    const beh = [...o._behaviors.keys()];
    const data = tryOr(() => prune(o.serialize().data, 5), {}) || {};
    const e = { id: o._blueprintID, b: beh, d: data };
    let w = 1, h = 1;
    if (o._behaviors.has('extendedTile')) {
      const ext = o._behaviors.get('extendedTile');
      w = tryOr(() => ext.width, 1); h = tryOr(() => ext.height, 1);
      e.ext = [w, h];
    }
    // big objects sit on their bottom cell and extend up and to the left
    const p = areaCentre(c - w + 1, r - h + 1, c, r, G);
    e.x = p.x; e.y = p.y;
    if (o._behaviors.has('harvestable')) {
      const hb = o._behaviors.get('harvestable');
      const hs = tryOr(() => hb.serialize(), {}) || {};
      e.harvest = { depleted: !!hs.depleted, harvests: hs.harvests, duration: hs.duration, removalCost: prune(hs.removalCost, 3) };
    }
    for (const k of Object.keys(data)) {
      const v = data[k];
      if (isObj(v) && typeof v.timerId === 'number') {
        e.timers = e.timers || {};
        e.timers[k] = timerInfo(v.timerId);
      }
    }
    return e;
  }

  function inventory() {
    const out = {};
    const items = tryOr(() => A.g.inventory._model._inventoryItems, null);
    if (items && typeof items.forEach === 'function') {
      items.forEach((v, k) => { out[k] = v._amount; });
    } else {
      for (const k of ['coins', 'gems', 'crates', 'energy', 'tickets']) out[k] = tryOr(() => A.g.inventory.getAmount(k), null);
    }
    return out;
  }

  let popupSeq = 0;

  // the popup layer also hosts stray sprites (e.g. the tutorial hand) and toasts (quest progress
  // banners that slide in and fade out by themselves, can't be pressed or closed); real popups are named scenes
  function popups(G) {
    const layer = tryOr(() => A.g.navigation.popupLayer.container, null);
    if (!layer) return [];
    return layer.children.filter((p) => p.visible && (p._name || p.onClosed) && !('_toastState' in p)).map((p) => ({
      id: p.__fmvId || (p.__fmvId = ++popupSeq),
      name: p._name || p.name || p.constructor.name,
      locked: !!p._popupLocked,
      interactionsLocked: !!p._popupInteractionsLocked,
      state: p._state,
      tapToContinue: !!(p._tapToContinueText && p._tapToContinueText.worldVisible),
      ui: uiTree(p, G, 14),
    }));
  }

  function textOf(o, depth) {
    if (depth < 0 || !o.visible) return '';
    let t = typeof o.text === 'string' ? o.text : '';
    if (o.children) for (const c of o.children) { const s = textOf(c, depth - 1); if (s) t += (t ? ' ' : '') + s; }
    return t.replace(/\s+/g, ' ').trim();
  }

  function texOf(o, depth) {
    if (depth < 0) return [];
    const out = [];
    const id = o.texture && o.texture.textureCacheIds ? o.texture.textureCacheIds[0] : null;
    if (id && o.visible) out.push(id);
    if (o.children) for (const c of o.children) out.push(...texOf(c, depth - 1));
    return out;
  }

  // a gem, cash or video icon anywhere on a button means pressing it costs something real
  const PRICED = /(gem|diamond|ruby|cash|dollar|video|premium)/i;

  // interactive, visible elements under a container with their labels and on-screen centre
  function uiTree(root, G, maxDepth) {
    const out = [];
    const walk = (o, d) => {
      if (!o.visible || d > maxDepth) return;
      if (o.interactive && o.worldVisible && o.worldAlpha > 0.05) {
        const b = boundsCss(o, G);
        if (b.w > 0 && b.h > 0) {
          const tex = texOf(o, 8);
          out.push({ name: o.name || '', cls: o.constructor.name, text: textOf(o, 6).slice(0, 80), tex: tex.slice(0, 6), priced: tex.some((t) => PRICED.test(t)), ...b });
        }
      }
      if (o.interactiveChildren !== false && o.children) for (const c of o.children) walk(c, d + 1);
    };
    walk(root, 0);
    return out;
  }

  function findDisplay(pred) {
    const stack = [A.g.canvas.stage];
    while (stack.length) {
      const o = stack.pop();
      if (o === A.sceneObj || !o.visible) continue;
      if (pred(o)) return o;
      if (o.children) for (const c of o.children) stack.push(c);
    }
    return null;
  }

  // the red "!" badge the game puts on buttons that have something to collect
  function attention(o) {
    const stack = [o];
    while (stack.length) {
      const n = stack.pop();
      if (!n.visible) continue;
      const tex = n.texture && n.texture.textureCacheIds ? n.texture.textureCacheIds[0] : '';
      if (tex === 'icon_notification_attention' && n.worldAlpha > 0.05) return true;
      if (n.region && n.region.name === 'icon_notification_attention' && n.worldAlpha > 0.05) return true;
      if (n.children) for (const c of n.children) stack.push(c);
    }
    return false;
  }

  function hud(G) {
    const out = {};
    const stack = [A.g.canvas.stage];
    while (stack.length) {
      const o = stack.pop();
      if (o === A.sceneObj) continue;
      if (o.interactive && o.name && o.worldVisible) {
        const b = boundsCss(o, G);
        // buttons the game slides off-screen (e.g. during the tutorial) don't count
        if (b.w > 0 && b.x > G.l && b.x < G.l + G.w && b.y > G.t && b.y < G.t + G.h) {
          // badges are drawn either inside the button or as an unnamed sibling next to it
          const badges = [o, ...((o.parent && o.parent.children) || []).filter((c) => c !== o && !(c.interactive && c.name))];
          out[o.name] = { ...b, text: textOf(o, 6).slice(0, 40), attention: badges.some(attention) };
        }
      }
      if (o.children) for (const c of o.children) stack.push(c);
    }
    return out;
  }

  function tutorial(G) {
    const ob = A.s.onboarding;
    const wl = A.s.interactionWhitelistService;
    const cellOf = (c) => (c ? [c.column, c.row] : null);
    const drags = tryOr(() => val(wl, 'dragWhitelist'), []) || [];
    const taps = tryOr(() => val(wl, 'tapWhitelist'), []) || [];
    let hand = null;
    const sprite = findDisplay((o) => o.worldAlpha > 0.05 && o.texture && o.texture.textureCacheIds
      && String(o.texture.textureCacheIds[0]).startsWith('hand_onboarding') && o.worldVisible);
    if (sprite) hand = boundsCss(sprite, G);
    return {
      track: tryOr(() => val(ob, 'activeTrackKey'), null) || null,
      enabled: tryOr(() => val(ob, 'isOnboardingEnabled'), null),
      blockDrags: !!tryOr(() => wl._blockAllDrags, false),
      blockTaps: !!tryOr(() => wl._blockAllTaps, false),
      drags: drags.map((d) => ({ from: cellOf(d.start), to: cellOf(d.end) })),
      taps: taps.map(cellOf),
      hand,
      dialog: !!tryOr(() => A.g.navigation.isDialogOpen(), false),
    };
  }

  A.snapshot = function (opts) {
    opts = opts || {};
    if (!A.g) return { ok: false, error: 'not initialised' };
    const G = geom();
    const snap = {
      ok: true,
      t: Date.now(),
      view: { w: G.w, h: G.h, l: G.l, t: G.t, vw: G.vw, vh: G.vh },
      inv: inventory(),
      level: tryOr(() => A.g.experience.getLevel(), null),
      exp: tryOr(() => A.g.experience.getCurrentExp(), null),
      nextExp: tryOr(() => A.g.experience.getNextLevelExp(), null),
      visiting: !!tryOr(() => val(A.g.friends, 'isVisitingFriend'), false),
      connected: tryOr(() => val(A.g.connection, 'isConnected'), null),
      focused: tryOr(() => val(A.g.pageVisibility, 'isFocused'), null),
      popups: popups(G),
      popupOpen: !!tryOr(() => A.g.navigation.isAnyPopupOpen(), false),
      unclaimedDiscoveries: tryOr(() => A.g.discovery.getUnclaimedItems().length, 0),
      hud: hud(G),
    };
    // a scene without its map built yet counts as still loading
    if (!refreshScene() || !mapContainer()) { snap.scene = null; return snap; }
    snap.scene = A.sceneObj.name || '';
    snap.camera = A.camera();
    // on-screen height of one tile at the current zoom
    snap.tileH = tryOr(() => (vertex(1, 1).y - vertex(0, 0).y) * G.sy, null);
    snap.tutorial = tutorial(G);
    const gw = A.s.gameWorkers;
    let free = tryOr(() => val(gw, 'getTotalFreeWorkers'), null);
    if (typeof free !== 'number') free = tryOr(() => val(gw, 'freeWorkerCount'), null);
    if (typeof free !== 'number') free = tryOr(() => (val(gw, 'hasFreeWorker') ? 1 : 0), null);
    snap.workers = { free, total: tryOr(() => val(gw, 'workerAmount'), null) };
    const cells = [];
    const empty = [];
    let locked = 0;
    for (const cell of A.s.mapGrid.cells.values()) {
      const o = cell._content;
      if (!o) { const p = cellPoint(cell.column, cell.row, G); empty.push([cell.column, cell.row, p.x, p.y]); continue; }
      if (o._behaviors.has('areaLock')) { locked++; continue; }
      if (o._behaviors.has('extendedTileChild')) continue;
      const e = objectState(o, cell.column, cell.row, G);
      e.c = cell.column; e.r = cell.row;
      cells.push(e);
    }
    snap.cells = cells;
    snap.empty = empty;
    snap.locked = locked;
    snap.orders = (tryOr(() => A.s.ordersService.getCurrentOrders(), []) || []).map((o) => ({
      building: o.buildingID, recipe: o.recipe, state: o.state, timer: typeof o.timerId === 'number' ? timerInfo(o.timerId) : null,
    }));
    snap.buildings = buildings();
    snap.popouts = popouts(G);
    snap.areas = areas(G);
    snap.mapUi = mapUi(G);
    return snap;
  };

  const CAMERA = new Set(['cameraView', 'worldView']);

  // named buttons drawn in the world itself, like the "Buy" bubble over purchasable land
  function mapUi(G) {
    const out = [];
    const stack = [A.sceneObj];
    while (stack.length) {
      const o = stack.pop();
      if (!o.visible || o._behaviors) continue;
      if (o.interactive && o.name && !CAMERA.has(o.name) && o.worldAlpha > 0.05 && o.worldVisible) {
        const b = boundsCss(o, G);
        if (b.w > 4 && b.h > 4) out.push({ name: o.name, text: textOf(o, 6).slice(0, 40), ...b });
      }
      if (o.children) for (const c of o.children) stack.push(c);
    }
    return out;
  }

  // small speech-bubble menus that open over board objects (e.g. "Coins 1 [Collect]")
  function popouts(G) {
    const root = tryOr(() => A.s.popout._targetContainer, null);
    if (!root) return [];
    const out = [];
    for (const p of root.children) {
      if (!p.visible || p.worldAlpha < 0.05) continue;
      const b = boundsCss(p, G);
      if (b.w < 4 || b.h < 4) continue;
      out.push({ ...b, text: textOf(p, 6).slice(0, 80), ui: uiTree(p, G, 10).filter((e) => e.w > 4) });
    }
    return out;
  }

  function areas(G) {
    const svc = [['land', A.s.mapAreaService], ['premium', A.s.premiumAreaService]];
    const out = [];
    for (const [kind, ms] of svc) {
      for (const a of tryOr(() => ms.getMapAreas(), []) || []) {
        if (a.state !== 1 && a.state !== 3) continue;
        const req = {};
        for (const r of (a.requirements && a.requirements.buy) || []) req[r.key] = r.amount;
        const pts = a.cells.map((c) => cellPoint(c.column, c.row, G));
        const x = pts.reduce((t, p) => t + p.x, 0) / (pts.length || 1);
        const y = pts.reduce((t, p) => t + p.y, 0) / (pts.length || 1);
        out.push({ id: a.id, kind, state: a.state, req, x, y, cells: a.cells.map((c) => [c.column, c.row]) });
      }
    }
    return out;
  }

  function buildings() {
    const b = A.g.buildings;
    const ids = new Set([...(tryOr(() => val(b, 'activeBuildings'), []) || []), ...(tryOr(() => val(b, 'inactiveBuildings'), []) || [])]);
    const out = {};
    for (const id of ids) {
      out[id] = {
        active: !!tryOr(() => b.isBuildingActive(id), false),
        state: tryOr(() => b.getBuildingState(id), null),
        cooldown: !!tryOr(() => b.isBuildingInCoolDownState(id), false),
        upgrading: !!tryOr(() => b.isBuildingUpgrading(id), false),
        level: tryOr(() => b.getBuildingLevel(id), null),
        upgradeCost: tryOr(() => prune(b.getUpgradeCost(id), 4), null),
      };
    }
    return out;
  }

  A.recipes = function () {
    const b = A.g.buildings;
    const unlocked = new Set(tryOr(() => A.g.experience.getAvailableRecipes(), []) || []);
    const out = {};
    for (const bid of tryOr(() => val(b, 'allBuildingsWithRecipes'), []) || []) {
      for (const rid of tryOr(() => b.getAvailableRecipes(bid), []) || []) {
        const r = tryOr(() => A.g.recipes.getRecipe(rid), null);
        if (!r) continue;
        out[rid] = {
          building: bid,
          duration: r.duration,
          ingredients: (r.ingredients || []).map((i) => ({ key: i.key, amount: i.amount })),
          reward: r.reward || [],
          image: r.image ? r.image.alias : null,
          unlocked: unlocked.has(rid),
        };
      }
    }
    return out;
  };

  // static blueprint database, fetched once per session
  A.blueprints = function () {
    const bc = A.g.blueprintCollection;
    const gs = A.g.blueprintGraphService;
    const out = {};
    for (const id of bc.allBlueprintsIDs) {
      const bp = tryOr(() => bc.getBlueprint(id), null);
      if (!bp) continue;
      const comps = {};
      for (const [k, v] of Object.entries(bp.components || {})) {
        if (k === 'sprite' || k === 'sounds' || k === 'spine') continue;
        comps[k] = v === null ? {} : JSON.parse(JSON.stringify(v));
      }
      const e = { id, extends: bp.extends || null, components: comps };
      const node = tryOr(() => gs.getNodeFromBlueprintID(id), null);
      if (node) {
        e.graph = tryOr(() => gs.getGraphNameFromBlueprintID(id), null);
        e.tier = node.tier;
        e.next = node.next ? node.next.ID : null;
        e.prev = node.previous ? node.previous.ID : null;
        e.harvestable = !!node.isHarvestable;
        e.collectItem = node.collectItem || null;
      }
      out[id] = e;
    }
    return out;
  };

  // leave a friend's farm (what the home button does)
  A.goHome = function () {
    return tryOr(() => { A.g.transition.goToOwnFarm(); return true; }, false);
  };

  // fallback for popups whose buttons we can't identify: same as pressing their X
  A.closeTopPopup = function () {
    return tryOr(() => { A.g.navigation.closeTopPopup(); return true; }, false);
  };

  window.__fmvAgent = A;
  return 'installed';
})();
