/* Presentation and discovery controls. Existing collection rules remain in app.js. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const ui = { status: 'all', rarity: '', sort: 'updated', view: 'collection' };
  let preservedArchiveOrder = null;
  let archiveOrderContext = null;
  let archiveProgress = new Map();
  let toastTimer;
  let refreshingDetail = false;
  const detailHome = $('character-detail').parentElement;
  const drawerLayer = $('detail-drawer');
  const drawerBody = $('detail-drawer-body');
  let drawerOpen = false;
  let drawerCharacterId = null;
  let drawerReturnFocus = null;
  let activeModal = null, returnFocus = null;
  const viewStateKey = `azurLaneWorkspaceState:v1:${location.pathname}`;
  const scrollContainers = { acquisition: $('acquisition-filter'), characters: characterList, detail: detailHome, drawer: drawerBody };
  let scrollPositions = {};
  let viewStateReady = false;
  let viewStateTimer;

  function archiveQueryContext(query) {
    return JSON.stringify([query, currentCharacterAcquisitionFilter, ui.status, ui.rarity, ui.sort]);
  }
  function archiveProgressValue(c) {
    return JSON.stringify([parseInt(c.collected, 10) || 0, parseInt(c.shareableSkinCount, 10) || 0]);
  }
  function resetArchiveOrder() {
    preservedArchiveOrder = null;
    archiveOrderContext = null;
    archiveProgress.clear();
  }
  function syncArchiveOrder(orderContext) {
    if (archiveOrderContext !== null && archiveOrderContext !== orderContext) resetArchiveOrder();
    // Capture before rerender or pagehide so an immediate reload retains the
    // visible order even when a quantity update's animation frame is pending.
    if (archiveOrderContext === orderContext && characters.some(c => archiveProgress.has(c.id) && archiveProgress.get(c.id) !== archiveProgressValue(c))) {
      preservedArchiveOrder = [...characterList.querySelectorAll('.character-row')].map(row => row.dataset.id);
    }
  }
  function rememberScrollPositions() {
    if (!viewStateReady) return;
    scrollPositions.page = { x: window.scrollX, y: window.scrollY };
    for (const [name, element] of Object.entries(scrollContainers)) {
      // Hidden panels report zero in browsers. Keep their last visible position.
      if (!element.closest('.hidden')) scrollPositions[name] = { x: element.scrollLeft, y: element.scrollTop };
    }
  }
  function saveViewState() {
    clearTimeout(viewStateTimer);
    if (!viewStateReady) return;
    syncArchiveOrder(archiveQueryContext(searchInput.value.trim()));
    rememberScrollPositions();
    const state = {
      version: 1, view: ui.view,
      archive: { search: searchInput.value, status: ui.status, rarity: ui.rarity, sort: ui.sort, acquisition: currentCharacterAcquisitionFilter, order: preservedArchiveOrder },
      pending: { search: $('incomplete-search').value, rarity: currentRarityFilter, acquisition: currentAcquisitionFilter, hideUnavailable },
      selectedCharacterId: currentCharacterId, drawerOpen,
      scroll: scrollPositions
    };
    try { sessionStorage.setItem(viewStateKey, JSON.stringify(state)); } catch (_) { /* Viewing still works when session storage is disabled or full. */ }
  }
  function scheduleViewStateSave() {
    if (!viewStateReady) return;
    clearTimeout(viewStateTimer);
    viewStateTimer = setTimeout(saveViewState, 180);
  }
  function readViewState() {
    try {
      const state = JSON.parse(sessionStorage.getItem(viewStateKey));
      if (!state || state.version !== 1 || !['collection','incomplete'].includes(state.view)) return null;
      const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      const text = value => typeof value === 'string' ? value : '';
      const choice = (value, options, fallback) => options.includes(value) ? value : fallback;
      const rarities = ['', 'ultra_rare','super_rare','elite','rare','common'];
      const archive = object(state.archive), pending = object(state.pending), scroll = object(state.scroll);
      const point = value => {
        const p = object(value);
        const offset = n => Number.isFinite(n) && n >= 0 ? n : 0;
        return { x: offset(p.x), y: offset(p.y) };
      };
      return {
        view: state.view,
        archive: { search: text(archive.search), status: choice(archive.status, ['all','pending','completed','shared'], 'all'), rarity: choice(archive.rarity, rarities, ''), sort: choice(archive.sort, ['updated','name','remaining'], 'updated'), acquisition: text(archive.acquisition), order: Array.isArray(archive.order) ? [...new Set(archive.order.filter(id => typeof id === 'string'))] : null },
        pending: { search: text(pending.search), rarity: choice(pending.rarity, rarities, ''), acquisition: text(pending.acquisition), hideUnavailable: pending.hideUnavailable === true },
        selectedCharacterId: text(state.selectedCharacterId), drawerOpen: state.drawerOpen === true,
        scroll: Object.fromEntries(['page', ...Object.keys(scrollContainers)].map(name => [name, point(scroll[name])]))
      };
    } catch (_) { return null; }
  }

  function syncOverlayState() {
    const blocked = drawerOpen || !!activeModal;
    document.querySelector('.app-main').inert = blocked;
    document.querySelector('.app-header').inert = blocked;
    drawerLayer.inert = !!activeModal;
    document.body.style.overflow = blocked ? 'hidden' : '';
  }
  function openDetailDrawer(id) {
    rememberScrollPositions();
    const changedCharacter = drawerCharacterId !== id;
    if (!drawerOpen) {
      drawerReturnFocus = document.activeElement;
      drawerBody.append($('character-detail'));
      drawerOpen = true;
      drawerLayer.classList.remove('hidden');
      syncOverlayState();
      requestAnimationFrame(() => { if (drawerOpen && !activeModal) $('close-detail-drawer').focus({preventScroll:true}); });
    }
    drawerCharacterId = id;
    if (changedCharacter) drawerBody.scrollTop = 0;
    scheduleViewStateSave();
  }
  function closeDetailDrawer(restoreFocus = true) {
    if (!drawerOpen) return;
    rememberScrollPositions();
    const triggerId = drawerCharacterId;
    drawerOpen = false;
    drawerCharacterId = null;
    detailHome.append($('character-detail'));
    drawerLayer.classList.add('hidden');
    syncOverlayState();
    if (restoreFocus && !activeModal) {
      const trigger = [...$('incomplete-list').querySelectorAll('[data-detail-character-id]')].find(el => el.dataset.detailCharacterId === triggerId);
      const destination = trigger || (drawerReturnFocus?.isConnected && drawerReturnFocus !== document.body ? drawerReturnFocus : $('tab-incomplete'));
      destination.focus({preventScroll:true});
    }
    drawerReturnFocus = null;
    scheduleViewStateSave();
  }
  $('close-detail-drawer').onclick = () => closeDetailDrawer();
  drawerLayer.querySelector('.character-drawer-backdrop').onclick = () => { if (!activeModal) closeDetailDrawer(); };
  function toast(message) {
    clearTimeout(toastTimer);
    $('toast-region').replaceChildren();
    const item = document.createElement('div'); item.className = 'toast'; item.textContent = message;
    $('toast-region').append(item);
    toastTimer = setTimeout(() => $('toast-region').replaceChildren(), 3200);
  }
  window.notifyTrackerMessage = toast;
  // Validate first, then show an explicit replacement summary inside the import form.
  let pendingImportSignature = '';
  const importReview = document.createElement('div');
  importReview.id = 'import-review'; importReview.className = 'import-review hidden'; importReview.setAttribute('role','status');
  $('import-data').parentElement.after(importReview);
  const clearImportReview = () => { pendingImportSignature=''; importReview.classList.add('hidden'); $('confirm-import-btn').textContent='校验并预览'; };
  window.confirmTrackerImport = data => {
    const signature = JSON.stringify(data);
    if(signature === pendingImportSignature) {pendingImportSignature=''; return true;}
    pendingImportSignature=signature;
    importReview.textContent = `校验通过：${data.characters.length} 位角色、${data.skins.length} 件换装。确认后将替换当前 ${characters.length} 位角色、${skins.length} 件换装。可通过撤销恢复当前角色与换装。`;
    importReview.classList.remove('hidden'); importReview.classList.remove('has-error');
    $('confirm-import-btn').textContent='确认替换并导入';
    return false;
  };
  window.showTrackerImportError = message => {
    clearImportReview(); importReview.textContent='导入失败：'+message; importReview.classList.remove('hidden'); importReview.classList.add('has-error');
  };
  $('import-data').addEventListener('input',clearImportReview);
  $('import-file').addEventListener('change',clearImportReview);
  $('import-btn').addEventListener('click',clearImportReview);
  window.onTrackerSaveState = (state, message) => {
    const el = $('save-status'); el.dataset.state = state;
    el.replaceChildren();
    const icon = document.createElement('i'); icon.className = 'fa ' + (state === 'error' ? 'fa-exclamation-circle' : state === 'saving' ? 'fa-circle-o-notch fa-spin' : 'fa-check-circle');
    el.append(icon, document.createTextNode(message || ({saving:'正在保存…', saved:'已保存到此浏览器',error:'保存失败，请导出备份'}[state])));
    el.title = message || el.textContent;
    if (state === 'error') toast(message || '保存未完成，请立即导出备份。');
  };
  if (window.trackerSaveState) window.onTrackerSaveState(window.trackerSaveState.state, window.trackerSaveState.message);

  function updateOverview() {
    const owned = skins.filter(s => s.owned).length;
    const complete = characters.filter(isCharacterCompleted).length;
    const shared = characters.filter(isCharacterCompletedViaShareable).length;
    $('stat-characters').textContent = characters.length.toLocaleString();
    $('stat-skins').textContent = skins.length.toLocaleString();
    $('stat-owned').textContent = owned.toLocaleString();
    $('stat-completed').textContent = complete.toLocaleString();
    $('stat-pending').textContent = (characters.length - complete).toLocaleString();
    $('stat-shared').textContent = shared.toLocaleString();
    $('tab-total').textContent = characters.length;
    $('tab-pending').textContent = characters.filter(c => getSkinsByCharacter(c.id).length > 0 && !isCharacterCompleted(c)).length;
    $('owned-progress').style.width = (skins.length ? owned / skins.length * 100 : 0) + '%';
    $('owned-caption').textContent = skins.length ? `已拥有 ${Math.round(owned / skins.length * 100)}% · 还有 ${skins.length - owned} 件待收` : '添加换装，开始记录';
    updateUncollectedValueDisplay();
  }
  function clearDetail() {
    closeDetailDrawer(false);
    currentCharacterId = null;
    $('character-detail').classList.add('hidden');
    $('detail-empty').classList.remove('hidden');
  }
  function matchesAcquisition(char) {
    const f = currentCharacterAcquisitionFilter;
    if (!f) return true;
    if (f === '未拥有换装角色') return hasNoOwnedSkins(char);
    if (isCharacterCompleted(char)) return false;
    const list = getSkinsByCharacter(char.id);
    if (f === '未拥有获取方式') return !(char.acquisition || '').trim();
    if (f === '拥有未使用的换装') return list.filter(s => s.owned).length > getEffectiveCollected(char);
    return (char.acquisition || '').split('、').map(x => x.trim()).includes(f);
  }
  renderCharacterList = function(filter = '') {
    const scrollTop = characterList.scrollTop;
    const query = filter.trim();
    const scores = new Map();
    let results = characters.filter(c => {
      if (!matchesAcquisition(c) || (ui.rarity && c.rarity !== ui.rarity)) return false;
      if (ui.status === 'pending' && isCharacterCompleted(c)) return false;
      if (ui.status === 'completed' && !isCharacterCompleted(c)) return false;
      if (ui.status === 'shared' && !isCharacterCompletedViaShareable(c)) return false;
      const score = query ? bestMatchScore(query, [c.name, c.acquisition || '', ...getSkinsByCharacter(c.id).map(s => s.name)]) : 0;
      scores.set(c.id, score); return score >= 0;
    });
    const orderContext = archiveQueryContext(query);
    syncArchiveOrder(orderContext);
    results.sort((a,b) => {
      const relevance = (scores.get(b.id) || 0) - (scores.get(a.id) || 0);
      if (query && relevance) return relevance;
      if (ui.sort === 'name') return a.name.localeCompare(b.name, 'zh-CN');
      if (ui.sort === 'remaining') {
        const remaining = Math.max(0, getTargetCount(b) - (parseInt(b.collected,10)||0)) - Math.max(0, getTargetCount(a) - (parseInt(a.collected,10)||0));
        if (remaining) return remaining;
      }
      return (Date.parse(b.updatedAt || b.createdAt) || 0) - (Date.parse(a.updatedAt || a.createdAt) || 0) || a.name.localeCompare(b.name, 'zh-CN');
    });
    if (preservedArchiveOrder) {
      const ranks = new Map(preservedArchiveOrder.map((id, index) => [id, index]));
      results.sort((a,b) => (ranks.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (ranks.get(b.id) ?? Number.MAX_SAFE_INTEGER));
      preservedArchiveOrder = results.map(c => c.id);
    }
    archiveOrderContext = orderContext;
    archiveProgress = new Map(results.map(c => [c.id, archiveProgressValue(c)]));
    $('result-count').textContent = `${results.length} / ${characters.length} 位`;
    const conditions = [];
    if(currentCharacterAcquisitionFilter) conditions.push(currentCharacterAcquisitionFilter);
    if(ui.status !== 'all') conditions.push($('status-select').selectedOptions[0].text);
    if(ui.rarity) conditions.push(getRarityText(ui.rarity));
    if(query) conditions.push(`“${query}”`);
    $('query-context').textContent = conditions.length ? `筛选中：${conditions.join(' · ')} · ${results.length} 个结果` : '支持中文、拼音和首字母查询';
    $('query-context').classList.toggle('filtered', conditions.length > 0);
    if (ui.view === 'collection' && !drawerOpen && currentCharacterId && !results.some(c => c.id === currentCharacterId)) clearDetail();
    characterList.replaceChildren();
    if (!results.length) {
      const empty = document.createElement('div'); empty.className = 'empty-state';
      empty.innerHTML = characters.length ? '<i class="fa fa-search"></i><h3>没有找到匹配的角色</h3><p>试试角色名、换装名或拼音首字母，<br>也可以清除筛选，查看全部档案。</p><button class="btn btn-outline" type="button">清除搜索与筛选</button>' : '<i class="fa fa-folder-open-o"></i><h3>从第一位角色开始</h3><p>新建角色，记录换装与收集进度。<br>已有收藏数据？可从顶部导入备份。</p><button class="btn btn-primary" type="button"><i class="fa fa-plus"></i>新建角色档案</button>';
      empty.querySelector('button').onclick = characters.length ? resetFilters : () => openCharacterModal();
      characterList.append(empty); return;
    }
    const fragment = document.createDocumentFragment();
    for (const c of results) {
      const list = getSkinsByCharacter(c.id), owned = list.filter(s => s.owned).length;
      const total = getTargetCount(c), current = getEffectiveCollected(c);
      const complete = isCharacterCompleted(c), shared = isCharacterCompletedViaShareable(c);
      const row = document.createElement('button'); row.type = 'button'; row.className = `character-row ${currentCharacterId === c.id ? 'bg-blue-100' : ''}`; row.dataset.id = c.id;
      row.setAttribute('aria-pressed', String(currentCharacterId === c.id));
      row.setAttribute('aria-label', `${c.name}，${getRarityText(c.rarity)}，收集 ${current}/${total}，已拥有 ${owned} 件换装`);
      const tags = (c.acquisition || '').split('、').filter(Boolean);
      const knownRarity = ['ultra_rare','super_rare','elite','rare','common'].includes(c.rarity) ? c.rarity : 'common';
      row.innerHTML = `<div class="character-row-top"><div class="character-identity"><span class="character-name">${escapeHtml(c.name)}</span><span class="rarity-tag rarity-${knownRarity}">${escapeHtml(getRarityText(c.rarity))}</span></div><span class="row-progress ${complete ? 'complete' : shared ? 'shared' : ''}" title="${complete ? '独立完成' : shared ? '共享达标' : '当前收集 / 目标'}"><i class="fa ${complete ? 'fa-check-circle' : shared ? 'fa-adjust' : 'fa-circle-o'}"></i>${current}<small> / ${total}</small></span></div><div class="character-row-bottom"><span class="row-tags">${tags.length ? tags.map(t => `<span>${escapeHtml(t)}</span>`).join('') : '尚未填写获取方式'}</span><span class="row-skins">换装 ${owned} / ${list.length} <i class="fa fa-angle-right"></i></span></div><div class="row-meter"><span style="width:${total ? Math.min(100,current / total * 100) : 100}%"></span></div>`;
      row.title = tags.join(' · ');
      row.onclick = () => showCharacterDetail(c.id, window.matchMedia('(max-width:680px)').matches);
      fragment.append(row);
    }
    characterList.append(fragment); characterList.scrollTop = scrollTop;
  };

  const originalShowDetail = showCharacterDetail;
  showCharacterDetail = function(id, scroll = false) {
    if (!characters.some(c => c.id === id)) { clearDetail(); return; }
    if (ui.view === 'incomplete' && !refreshingDetail) openDetailDrawer(id);
    originalShowDetail(id, false);
    $('detail-empty').classList.add('hidden');
    for(const row of characterList.querySelectorAll('.character-row')) {
      row.setAttribute('aria-pressed', String(row.dataset.id === id));
      row.classList.toggle('bg-blue-100', row.dataset.id === id);
    }
    const list = getSkinsByCharacter(id);
    $('skin-count-label').textContent = `${list.filter(s => s.owned).length} / ${list.length}`;
    $('acquisition-input').setAttribute('aria-label', '角色获取方式');
    $('acquisition-input').placeholder = '多个来源用顿号分隔';
    $('collection-progress-container').querySelectorAll('input').forEach(input => input.setAttribute('aria-label', '现有收集数量（含共享）'));
    $('extra-stats-container').querySelectorAll('input').forEach(input => input.setAttribute('aria-label', '可共享换装角色数量'));
    const rows = [...$('skins-list').children];
    rows.forEach((row, index) => {
      const skin = list[index]; if (!skin) return;
      const remove = row.querySelector('button');
      if (remove) {
        remove.title = `删除换装：${skin.name}`; remove.setAttribute('aria-label', remove.title);
        const edit = document.createElement('button'); edit.className = 'skin-edit text-gray-400 hover:text-primary'; edit.type = 'button'; edit.title = `编辑换装：${skin.name}`; edit.setAttribute('aria-label', edit.title); edit.innerHTML = '<i class="fa fa-pencil"></i>';
        edit.onclick = e => { e.stopPropagation(); openSkinModal(skin.id); };
        remove.before(edit);
      }
    });
    if (scroll && !drawerOpen && ui.view === 'collection') $('character-detail').scrollIntoView({behavior:'smooth',block:'start'});
    scheduleViewStateSave();
  };

  const originalRenderAll = renderAll;
  renderAll = function() {
    const closedMissingDrawer = drawerOpen && (!currentCharacterId || !characters.some(c => c.id === drawerCharacterId));
    if (closedMissingDrawer) closeDetailDrawer(false);
    if (currentCharacterId && !characters.some(c => c.id === currentCharacterId)) clearDetail();
    refreshingDetail = true;
    try { originalRenderAll(); } finally { refreshingDetail = false; }
    updateOverview();
    $('detail-empty').classList.toggle('hidden', !characterDetail.classList.contains('hidden'));
    if (closedMissingDrawer && !activeModal) $('tab-incomplete').focus({preventScroll:true});
    scheduleViewStateSave();
  };
  function setView(view) {
    rememberScrollPositions();
    if (drawerOpen) closeDetailDrawer(false);
    ui.view = view;
    $('collection-workspace').classList.toggle('hidden', view !== 'collection');
    $('incomplete-panel').classList.toggle('hidden', view !== 'incomplete');
    for (const name of ['collection','incomplete']) {
      const tab = $('tab-' + name); tab.classList.toggle('active', view === name); tab.setAttribute('aria-selected', String(view === name)); tab.tabIndex = view === name ? 0 : -1;
    }
    if (view === 'collection') filterCharacters();
    if (viewStateReady) {
      for (const name of ['characters','detail']) {
        const position = scrollPositions[name];
        if (position && !scrollContainers[name].closest('.hidden')) {
          scrollContainers[name].scrollLeft = position.x;
          scrollContainers[name].scrollTop = position.y;
        }
      }
    }
    scheduleViewStateSave();
  }
  function resetFilters() {
    resetArchiveOrder();
    currentAcquisitionFilter = ''; currentCharacterAcquisitionFilter = ''; currentRarityFilter = '';
    ui.rarity = ''; ui.status = 'all';
    $('status-select').value = 'all'; $('archive-rarity').value = '';
    searchInput.value = ''; $('incomplete-search').value = '';
    $('clear-search-btn').classList.add('hidden'); $('clear-incomplete-search-btn').classList.add('hidden');
    hideUnavailable = false;
    $('hide-unavailable-btn').classList.remove('btn-secondary'); $('hide-unavailable-btn').classList.add('btn-outline');
    $('hide-unavailable-label').textContent = '隐藏无法获取角色';
    setRarityFilter(''); renderAll(); renderAcquisitionChart();
  }
  $('new-character-btn').onclick = () => openCharacterModal();
  $('reset-filters').onclick = resetFilters;
  for (const [id,key] of [['status-select','status'],['archive-rarity','rarity'],['sort-select','sort']]) $(id).addEventListener('change', e => {resetArchiveOrder();ui[key]=e.target.value;characterList.scrollTop=0;filterCharacters();});
  searchInput.addEventListener('input', () => {characterList.scrollTop = 0;});
  for(const view of ['collection','incomplete']) {
    $('tab-'+view).onclick = () => setView(view);
    $('tab-'+view).addEventListener('keydown',e => {
      if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) {e.preventDefault();const next=e.key==='Home'?'collection':e.key==='End'?'incomplete':ui.view==='collection'?'incomplete':'collection';setView(next);$('tab-'+next).focus();}
    });
  }
  const activityObserver = new MutationObserver(() => $('recent-activity').setAttribute('aria-expanded', String(!$('recent-activity-dropdown').classList.contains('hidden'))));
  activityObserver.observe($('recent-activity-dropdown'), {attributes:true, attributeFilter:['class']});
  // Modal focus is contained, background is inert, and returning focus is predictable.
  const modals = ['character','skin','quick-add','import'].map(x=>$(x+'-modal'));
  const titles = ['modal-title','skin-modal-title','quick-add-title','import-title'];
  $('import-modal').querySelector('h3').id = 'import-title';
  modals.forEach((modal,i) => {modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-labelledby',titles[i]);});
  const modalObserver = new MutationObserver(() => {
    const open = modals.find(m => !m.classList.contains('hidden')) || null;
    if (open === activeModal) return;
    if (open) {
      returnFocus = document.activeElement; activeModal = open;
      syncOverlayState();
      requestAnimationFrame(() => { const first = open.querySelector('input:not([type=hidden]),textarea,select,button'); first?.focus(); });
    } else {
      activeModal = null;
      syncOverlayState();
      if (returnFocus?.isConnected && (!drawerOpen || drawerLayer.contains(returnFocus))) returnFocus.focus({preventScroll:true});
      else if (drawerOpen) $('close-detail-drawer').focus({preventScroll:true});
      else $('tab-' + ui.view).focus({preventScroll:true});
    }
  });
  modals.forEach(m=>modalObserver.observe(m,{attributes:true,attributeFilter:['class']}));
  document.addEventListener('keydown', e => {
    const activeOverlay = activeModal || (drawerOpen ? drawerLayer : null);
    if (activeOverlay) {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (activeModal) activeModal.classList.add('hidden');
        else closeDetailDrawer();
        return;
      }
      if (e.key === 'Tab') {
        const focusable = [...activeOverlay.querySelectorAll('button:not([disabled]),input:not([type=hidden]):not([disabled]),select,textarea,[tabindex="0"]')].filter(el => el.getClientRects().length);
        const first=focusable[0],last=focusable.at(-1);
        if (e.shiftKey && document.activeElement===first) {e.preventDefault();last?.focus();}
        else if(!e.shiftKey && document.activeElement===last) {e.preventDefault();first?.focus();}
      }
      return;
    }
    const editing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if(e.key==='/' && !editing && !e.ctrlKey && !e.metaKey && !e.altKey) {e.preventDefault();(ui.view==='collection'?searchInput:$('incomplete-search')).focus();}
    if(e.key==='Escape') {$('recent-activity-dropdown').classList.add('hidden');}
  });
  function restoreWorkspace() {
    const saved = readViewState();
    if (!saved) {
      renderAll(); setView('collection');
      viewStateReady = true;
      return;
    }
    // Take ownership before the browser's load-time scroll restoration runs.
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    Object.assign(ui, { view: saved.view, status: saved.archive.status, rarity: saved.archive.rarity, sort: saved.archive.sort });
    preservedArchiveOrder = saved.archive.order;
    searchInput.value = saved.archive.search;
    $('status-select').value = ui.status;
    $('archive-rarity').value = ui.rarity;
    $('sort-select').value = ui.sort;
    $('incomplete-search').value = saved.pending.search;
    currentCharacterAcquisitionFilter = saved.archive.acquisition;
    currentAcquisitionFilter = saved.pending.acquisition;
    currentRarityFilter = saved.pending.rarity;
    hideUnavailable = saved.pending.hideUnavailable;
    $('hide-unavailable-btn').classList.toggle('btn-secondary', hideUnavailable);
    $('hide-unavailable-btn').classList.toggle('btn-outline', !hideUnavailable);
    $('hide-unavailable-label').textContent = hideUnavailable ? '显示无法获取角色' : '隐藏无法获取角色';
    $('clear-search-btn').classList.toggle('hidden', !searchInput.value.trim());
    $('clear-incomplete-search-btn').classList.toggle('hidden', !$('incomplete-search').value.trim());
    currentCharacterId = characters.some(c => c.id === saved.selectedCharacterId) ? saved.selectedCharacterId : null;
    setView(ui.view);
    setRarityFilter(currentRarityFilter);
    renderAll();
    renderAcquisitionChart();
    if (ui.view === 'incomplete' && saved.drawerOpen && currentCharacterId) showCharacterDetail(currentCharacterId);
    scrollPositions = saved.scroll;

    // Layout and icon fonts must settle before applying pixel offsets. A real
    // interaction cancels the pending restore so it cannot pull the user back.
    let finished = false;
    const interactionEvents = ['pointerdown','touchstart','wheel','keydown'];
    function finish(restoreScroll) {
      if (finished) return;
      finished = true;
      for (const event of interactionEvents) window.removeEventListener(event, cancelRestore, true);
      if (restoreScroll) {
        for (const [name, element] of Object.entries(scrollContainers)) {
          if (!element.closest('.hidden')) {
            element.scrollLeft = saved.scroll[name].x;
            element.scrollTop = saved.scroll[name].y;
          }
        }
        window.scrollTo({ left: saved.scroll.page.x, top: saved.scroll.page.y, behavior: 'instant' });
      }
      viewStateReady = true;
      scheduleViewStateSave();
    }
    const cancelRestore = () => finish(false);
    for (const event of interactionEvents) window.addEventListener(event, cancelRestore, { capture: true, passive: true });
    const loaded = document.readyState === 'complete' ? Promise.resolve() : new Promise(resolve => window.addEventListener('load', resolve, { once: true }));
    Promise.all([loaded, document.fonts?.ready]).then(() => {
      requestAnimationFrame(() => requestAnimationFrame(() => finish(true)));
    });
  }
  // Capture also sees controls that stop propagation. Delay saves until their
  // handlers/render have applied; lifecycle saves capture even an immediate reload.
  for (const event of ['click','input','change','keydown','drop']) document.addEventListener(event, scheduleViewStateSave, true);
  window.addEventListener('scroll', scheduleViewStateSave, { capture: true, passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') { flushPendingSaves(); saveViewState(); }
  });
  window.addEventListener('pagehide', () => { flushPendingSaves(); saveViewState(); });
  // 宿主隐藏或销毁内嵌页面时，立即保存收藏与已提交的查看状态。
  window.addEventListener('azurpilot:collection-save', () => { flushPendingSaves(); saveViewState(); });
  restoreWorkspace();
})();
